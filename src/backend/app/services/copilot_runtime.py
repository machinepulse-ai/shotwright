"""Copilot SDK runtime manager for Shotwright chat sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from time import monotonic
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import uuid4

from copilot import CopilotClient, SubprocessConfig
from copilot.session import PermissionHandler
from pymongo import ReturnDocument

from app.config import settings
from app.database import (
    get_admin_collection,
    get_cache_collection,
    get_event_collection,
    get_message_collection,
    get_session_collection,
)
from app.models.session import ReasoningEffort
from app.services.agent_tools import build_shotwright_tools
from app.services.session_streams import (
    publish_message_deleted,
    publish_message_upsert,
    publish_session_updated,
    publish_timeline_event,
)

logger = logging.getLogger(__name__)

_IN_MEMORY_MODEL_CACHE_TTL_SECONDS = 60
_MONGO_MODEL_CACHE_TTL_SECONDS = 600


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {k: _serialize(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(v) for v in value]
    if hasattr(value, "model_dump"):
        return _serialize(value.model_dump())
    if hasattr(value, "__dict__"):
        return {k: _serialize(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _event_type_name(event) -> str:
    event_type = getattr(event, "type", "unknown")
    if isinstance(event_type, Enum):
        return event_type.value
    return str(event_type)


def _event_summary(event_type: str, data: dict) -> str:
    if event_type == "tool.execution_start":
        return f"Tool start: {data.get('tool_name') or data.get('toolName') or 'unknown'}"
    if event_type == "tool.execution_complete":
        tool_name = data.get("tool_name") or data.get("toolName") or "unknown"
        success = data.get("success")
        return f"Tool complete: {tool_name} ({'ok' if success else 'failed'})"
    if event_type == "session.task_complete":
        return data.get("summary") or "Agent task completed"
    if event_type == "session.error":
        return data.get("message") or data.get("error") or "Agent session error"
    if event_type.startswith("subagent."):
        return event_type
    if event_type == "permission.requested":
        return f"Permission requested: {data.get('permission_request', {}).get('kind', 'unknown')}"
    return event_type


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class _RuntimeHandle:
    def __init__(self, app_session_id: str, client: CopilotClient, session, unsubscribe) -> None:
        self.app_session_id = app_session_id
        self.client = client
        self.session = session
        self.unsubscribe = unsubscribe
        self.lock = asyncio.Lock()
        self.pending_tasks: set[asyncio.Task] = set()
        self.turn_state: _StreamingTurnState | None = None

    def track_task(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.pending_tasks.add(task)
        task.add_done_callback(self.pending_tasks.discard)


class _StreamingTurnState:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        self.content = ""
        self.version = 0
        self.finalized = False


class ShotwrightCopilotRuntimeManager:
    def __init__(self) -> None:
        self._runtimes: dict[str, _RuntimeHandle] = {}
        self._setup_lock = asyncio.Lock()
        self._models_cache: tuple[str, float, list[dict]] | None = None
        self._models_lock = asyncio.Lock()

    async def _resolve_github_token(self) -> str | None:
        if settings.github_token:
            return settings.github_token
        doc = await get_admin_collection().find_one({"_id": "settings"})
        token = doc.get("github_token") if doc else None
        return token or None

    async def _resolve_runtime_settings(self) -> dict[str, str | bool]:
        doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
        return {
            "copilot_cli_path": _first_non_empty(doc.get("copilot_cli_path"), settings.copilot_cli_path),
            "copilot_workspace_root": _first_non_empty(
                doc.get("copilot_workspace_root"),
                settings.copilot_workspace_root,
            )
            or "C:\\workspace",
            "copilot_use_logged_in_user": bool(
                doc.get("copilot_use_logged_in_user", settings.copilot_use_logged_in_user)
            ),
            "copilot_http_proxy": _first_non_empty(
                doc.get("copilot_http_proxy"),
                settings.copilot_http_proxy,
                os.environ.get("HTTP_PROXY"),
                os.environ.get("http_proxy"),
            ),
            "copilot_https_proxy": _first_non_empty(
                doc.get("copilot_https_proxy"),
                settings.copilot_https_proxy,
                os.environ.get("HTTPS_PROXY"),
                os.environ.get("https_proxy"),
            ),
            "copilot_no_proxy": _first_non_empty(
                doc.get("copilot_no_proxy"),
                settings.copilot_no_proxy,
                os.environ.get("NO_PROXY"),
                os.environ.get("no_proxy"),
            ),
        }

    def _build_subprocess_env(self, runtime_settings: dict[str, str | bool]) -> dict[str, str]:
        env = dict(os.environ)
        proxy_map = {
            "http_proxy": runtime_settings["copilot_http_proxy"],
            "HTTP_PROXY": runtime_settings["copilot_http_proxy"],
            "https_proxy": runtime_settings["copilot_https_proxy"],
            "HTTPS_PROXY": runtime_settings["copilot_https_proxy"],
            "no_proxy": runtime_settings["copilot_no_proxy"],
            "NO_PROXY": runtime_settings["copilot_no_proxy"],
        }
        for key, value in proxy_map.items():
            if isinstance(value, str) and value.strip():
                env[key] = value.strip()
        return env

    async def get_runtime_settings(self) -> dict[str, str | bool]:
        return await self._resolve_runtime_settings()

    async def resolve_default_session_settings(self) -> tuple[str, ReasoningEffort | None]:
        doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
        configured_model = _first_non_empty(doc.get("default_copilot_model"), settings.copilot_model) or settings.copilot_model
        configured_reasoning_effort = (
            doc["default_copilot_reasoning_effort"]
            if "default_copilot_reasoning_effort" in doc
            else settings.copilot_reasoning_effort
        )

        try:
            return await self.validate_model_choice(configured_model.strip(), configured_reasoning_effort)
        except Exception:
            logger.warning(
                "Falling back to application Copilot defaults because the configured admin defaults could not be validated"
            )
            return settings.copilot_model, settings.copilot_reasoning_effort

    def _build_client(self, github_token: str | None, runtime_settings: dict[str, str | bool]) -> CopilotClient:
        return CopilotClient(
            SubprocessConfig(
                github_token=github_token,
                cli_path=(runtime_settings["copilot_cli_path"] or None),
                cwd=str(runtime_settings["copilot_workspace_root"]),
                env=self._build_subprocess_env(runtime_settings),
                use_logged_in_user=bool(runtime_settings["copilot_use_logged_in_user"]),
            )
        )

    def _build_model_cache_key(self, runtime_settings: dict[str, str | bool], github_token: str | None) -> str:
        cache_input = {
            "github_token": github_token or "",
            "copilot_cli_path": runtime_settings["copilot_cli_path"],
            "copilot_workspace_root": runtime_settings["copilot_workspace_root"],
            "copilot_use_logged_in_user": runtime_settings["copilot_use_logged_in_user"],
        }
        digest = hashlib.sha256(json.dumps(cache_input, sort_keys=True).encode("utf-8")).hexdigest()
        return f"models:{digest}"

    async def list_available_models(self, force_refresh: bool = False) -> list[dict]:
        runtime_settings = await self._resolve_runtime_settings()
        github_token = await self._resolve_github_token()
        if not github_token and not runtime_settings["copilot_use_logged_in_user"]:
            raise ValueError("GitHub token is not configured for Copilot SDK")

        cache_key = self._build_model_cache_key(runtime_settings, github_token)
        if (
            not force_refresh
            and self._models_cache
            and self._models_cache[0] == cache_key
            and monotonic() - self._models_cache[1] < _IN_MEMORY_MODEL_CACHE_TTL_SECONDS
        ):
            return self._models_cache[2]

        async with self._models_lock:
            if (
                not force_refresh
                and self._models_cache
                and self._models_cache[0] == cache_key
                and monotonic() - self._models_cache[1] < _IN_MEMORY_MODEL_CACHE_TTL_SECONDS
            ):
                return self._models_cache[2]

            if not force_refresh:
                cached_doc = await get_cache_collection("copilot_model_cache").find_one(
                    {
                        "_id": cache_key,
                        "expires_at": {"$gt": _utcnow()},
                    }
                )
                if cached_doc and isinstance(cached_doc.get("models"), list):
                    normalized_models = cached_doc["models"]
                    self._models_cache = (cache_key, monotonic(), normalized_models)
                    return normalized_models

            client = self._build_client(github_token, runtime_settings)
            await client.start()
            try:
                models = await client.list_models()
            finally:
                await client.stop()

            normalized_models = []
            for model in models:
                supported_reasoning_efforts = [
                    effort
                    for effort in (model.supported_reasoning_efforts or [])
                    if effort in {"low", "medium", "high", "xhigh"}
                ]
                normalized_models.append(
                    {
                        "id": model.id,
                        "name": model.name,
                        "supports_reasoning_effort": bool(model.capabilities.supports.reasoning_effort),
                        "supported_reasoning_efforts": supported_reasoning_efforts,
                        "default_reasoning_effort": model.default_reasoning_effort,
                    }
                )

            await get_cache_collection("copilot_model_cache").replace_one(
                {"_id": cache_key},
                {
                    "_id": cache_key,
                    "models": normalized_models,
                    "created_at": _utcnow(),
                    "expires_at": _utcnow() + timedelta(seconds=_MONGO_MODEL_CACHE_TTL_SECONDS),
                },
                upsert=True,
            )
            self._models_cache = (cache_key, monotonic(), normalized_models)
            return normalized_models

    async def validate_model_choice(
        self,
        model_id: str,
        reasoning_effort: str | None,
    ) -> tuple[str, ReasoningEffort | None]:
        models = await self.list_available_models()
        selected = next((model for model in models if model["id"] == model_id), None)
        if not selected:
            raise ValueError(f"Unknown Copilot model: {model_id}")

        supported_reasoning_efforts = selected.get("supports_reasoning_effort") and selected.get(
            "supported_reasoning_efforts"
        ) or []

        if not supported_reasoning_efforts:
            return selected["id"], None

        if reasoning_effort is None:
            default_effort = selected.get("default_reasoning_effort")
            if default_effort in supported_reasoning_efforts:
                return selected["id"], default_effort
            return selected["id"], supported_reasoning_efforts[0]

        if reasoning_effort not in supported_reasoning_efforts:
            raise ValueError(
                f"Model {model_id} does not support reasoning effort {reasoning_effort}"
            )

        return selected["id"], reasoning_effort

    async def apply_session_settings(
        self,
        app_session_id: str,
        model_id: str,
        reasoning_effort: ReasoningEffort | None,
    ) -> None:
        runtime = self._runtimes.get(app_session_id)
        if not runtime:
            return

        async with runtime.lock:
            await runtime.session.set_model(model_id, reasoning_effort=reasoning_effort)
            runtime.track_task(
                self._persist_event(
                    app_session_id,
                    "session.model_updated",
                    {"model": model_id, "reasoning_effort": reasoning_effort},
                )
            )

    def _system_prompt(self) -> str:
        return (
            "You are Shotwright's After Effects operator. "
            "Your job is to help the user accomplish creative work by using the provided tools only for concrete runtime actions. "
            "Relevant repository skills loaded from the workspace are part of your operating instructions, not separate tools, and should be applied whenever they match the task. "
            "When the user explicitly mentions a skill or asks about a skill-defined workflow, answer directly from that skill before considering workspace inspection. "
            "Always inspect the workspace state before taking action when the current container, project, or render state is unclear. "
            "Do not claim that an After Effects action happened unless a tool succeeded. "
            "Prefer starting or reusing a container before JSX or render actions. "
            "If multiple uploaded projects or AEP files exist and the intended target is ambiguous, ask the user a concise clarification question. "
            "When rendering succeeds, mention the preview stream and export archive when relevant."
        )

    def _custom_agents(self, skill_names: list[str]) -> list[dict]:
        tool_names = [
            "inspect_workspace",
            "ensure_after_effects_container",
            "list_uploaded_projects",
            "select_active_project",
            "run_after_effects_jsx",
            "render_after_effects_project",
            "export_project_archive",
            "stop_after_effects_container",
        ]
        agent = {
            "name": "ae_operator",
            "display_name": "After Effects Operator",
            "description": "Controls Shotwright containers and runs After Effects tasks through guarded backend tools.",
            "tools": tool_names,
            "prompt": self._system_prompt(),
        }
        if skill_names:
            agent["skills"] = skill_names
        return [agent]

    def _resolve_skill_directories(self, runtime_settings: dict[str, str | bool]) -> list[str]:
        workspace_root = str(runtime_settings["copilot_workspace_root"])
        candidates = [
            os.path.join(workspace_root, ".github", "skills"),
            os.path.join(workspace_root, ".agents", "skills"),
            os.path.join(workspace_root, ".claude", "skills"),
        ]
        return [path for path in candidates if os.path.isdir(path)]

    def _resolve_skill_names(self, skill_directories: list[str]) -> list[str]:
        skill_names: set[str] = set()
        for directory in skill_directories:
            try:
                for entry in os.scandir(directory):
                    if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "SKILL.md")):
                        skill_names.add(entry.name)
            except FileNotFoundError:
                continue
        return sorted(skill_names)

    def _extract_delta_content(self, data) -> str:
        for attribute_name in ("delta_content", "content", "partial_output"):
            value = getattr(data, attribute_name, None)
            if isinstance(value, str) and value:
                return value
        return ""

    async def _persist_event(self, app_session_id: str, event_type: str, data: dict) -> None:
        if event_type in {
            "assistant.message",
            "assistant.message_delta",
            "assistant.reasoning",
            "assistant.reasoning_delta",
            "assistant.streaming_delta",
            "user.message",
        }:
            return
        doc = {
            "_id": str(uuid4()),
            "session_id": app_session_id,
            "type": event_type,
            "summary": _event_summary(event_type, data),
            "data": data,
            "created_at": _utcnow(),
        }
        await get_event_collection().insert_one(doc)
        await publish_timeline_event(doc)

    async def _persist_message(self, app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": str(uuid4()),
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "created_at": _utcnow(),
        }
        await get_message_collection().insert_one(doc)
        await publish_message_upsert(doc)
        return doc

    async def _sync_streaming_message(
        self,
        turn_state: _StreamingTurnState,
        *,
        content: str,
        version: int,
        streaming: bool,
        state: str,
    ) -> dict | None:
        updated_doc = await get_message_collection().find_one_and_update(
            {
                "_id": turn_state.message_id,
                "metadata.version": {"$lt": version},
            },
            {
                "$set": {
                    "content": content,
                    "metadata": {
                        "streaming": streaming,
                        "state": state,
                        "version": version,
                    },
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated_doc:
            await publish_message_upsert(updated_doc)
        return updated_doc

    async def _set_session_status(self, app_session_id: str, status: str, **extra) -> dict | None:
        updated_session = await get_session_collection().find_one_and_update(
            {"_id": app_session_id},
            {"$set": {"status": status, "updated_at": _utcnow(), **extra}},
            return_document=ReturnDocument.AFTER,
        )
        if updated_session:
            await publish_session_updated(app_session_id, updated_session)
        return updated_session

    async def _build_runtime(self, app_session_id: str) -> _RuntimeHandle:
        session_doc = await get_session_collection().find_one({"_id": app_session_id})
        if not session_doc:
            raise ValueError("Session not found")

        runtime_settings = await self._resolve_runtime_settings()
        github_token = await self._resolve_github_token()
        if not github_token and not runtime_settings["copilot_use_logged_in_user"]:
            raise ValueError("GitHub token is not configured for Copilot SDK")

        client = self._build_client(github_token, runtime_settings)
        await client.start()

        copilot_session_id = session_doc.get("copilot_session_id") or f"shotwright-{app_session_id}"
        default_model, default_reasoning_effort = await self.resolve_default_session_settings()
        session_model = session_doc.get("copilot_model") or default_model
        session_reasoning_effort = (
            session_doc["copilot_reasoning_effort"]
            if "copilot_reasoning_effort" in session_doc
            else default_reasoning_effort
        )
        skill_directories = self._resolve_skill_directories(runtime_settings)
        skill_names = self._resolve_skill_names(skill_directories)
        session_kwargs = {
            "on_permission_request": PermissionHandler.approve_all,
            "model": session_model,
            "reasoning_effort": session_reasoning_effort,
            "streaming": True,
            "available_tools": [],
            "tools": build_shotwright_tools(app_session_id),
            "system_message": {"mode": "replace", "content": self._system_prompt()},
            "custom_agents": self._custom_agents(skill_names),
            "agent": "ae_operator",
            "working_directory": str(runtime_settings["copilot_workspace_root"]),
            "skill_directories": skill_directories,
        }

        if session_doc.get("copilot_session_id"):
            session = await client.resume_session(copilot_session_id, **session_kwargs)
        else:
            session = await client.create_session(session_id=copilot_session_id, **session_kwargs)
            await self._set_session_status(app_session_id, session_doc.get("status") or "idle", copilot_session_id=session.session_id)

        def on_event(event):
            event_type = _event_type_name(event)
            event_data = getattr(event, "data", None)
            data = _serialize(getattr(event, "data", None)) or {}
            runtime = self._runtimes.get(app_session_id)
            if runtime:
                turn_state = runtime.turn_state
                if turn_state and not turn_state.finalized and event_type in {"assistant.message_delta", "assistant.streaming_delta"}:
                    delta = self._extract_delta_content(event_data)
                    if delta:
                        turn_state.content += delta
                        turn_state.version += 1
                        runtime.track_task(
                            self._sync_streaming_message(
                                turn_state,
                                content=turn_state.content,
                                version=turn_state.version,
                                streaming=True,
                                state="streaming",
                            )
                        )

                if turn_state and event_type == "assistant.message":
                    final_content = getattr(event_data, "content", None)
                    if isinstance(final_content, str) and final_content:
                        turn_state.content = final_content
                    turn_state.finalized = True
                    turn_state.version += 1
                    runtime.track_task(
                        self._sync_streaming_message(
                            turn_state,
                            content=turn_state.content,
                            version=turn_state.version,
                            streaming=False,
                            state="completed",
                        )
                    )

                runtime.track_task(self._persist_event(app_session_id, event_type, data))

        unsubscribe = session.on(on_event)
        handle = _RuntimeHandle(app_session_id, client, session, unsubscribe)
        return handle

    async def ensure_runtime(self, app_session_id: str) -> _RuntimeHandle:
        runtime = self._runtimes.get(app_session_id)
        if runtime:
            return runtime

        async with self._setup_lock:
            runtime = self._runtimes.get(app_session_id)
            if runtime:
                return runtime
            runtime = await self._build_runtime(app_session_id)
            self._runtimes[app_session_id] = runtime
            runtime.track_task(
                self._persist_event(app_session_id, "session.created", {"copilot_session_id": runtime.session.session_id})
            )
            return runtime

    async def send_message(self, app_session_id: str, content: str) -> dict:
        runtime = await self.ensure_runtime(app_session_id)
        async with runtime.lock:
            await self._persist_message(app_session_id, "user", content)
            assistant_doc = await self._persist_message(
                app_session_id,
                "assistant",
                "",
                {"streaming": True, "state": "pending", "version": 0},
            )
            runtime.turn_state = _StreamingTurnState(assistant_doc["_id"])
            await self._set_session_status(app_session_id, "running", last_error=None)
            try:
                response = await runtime.session.send_and_wait(content)
                turn_state = runtime.turn_state
                assistant_text = turn_state.content if turn_state else ""
                if response and getattr(response, "data", None):
                    assistant_text = getattr(response.data, "content", "") or assistant_text

                if turn_state and (assistant_text != turn_state.content or not turn_state.finalized):
                    turn_state.content = assistant_text
                    turn_state.finalized = True
                    turn_state.version += 1
                    await self._sync_streaming_message(
                        turn_state,
                        content=turn_state.content,
                        version=turn_state.version,
                        streaming=False,
                        state="completed",
                    )

                await self._set_session_status(app_session_id, "idle")
                if runtime.pending_tasks:
                    await asyncio.gather(*list(runtime.pending_tasks), return_exceptions=True)

                if runtime.turn_state:
                    assistant_doc = await get_message_collection().find_one({"_id": runtime.turn_state.message_id}) or assistant_doc

                return {
                    "assistant_message": assistant_doc,
                    "session_status": "idle",
                }
            except Exception as exc:
                logger.exception("Copilot turn failed for %s", app_session_id)
                if runtime.turn_state and runtime.turn_state.content.strip():
                    runtime.turn_state.finalized = True
                    runtime.turn_state.version += 1
                    await self._sync_streaming_message(
                        runtime.turn_state,
                        content=runtime.turn_state.content,
                        version=runtime.turn_state.version,
                        streaming=False,
                        state="error",
                    )
                else:
                    await get_message_collection().delete_one({"_id": assistant_doc["_id"]})
                    await publish_message_deleted(app_session_id, assistant_doc["_id"])
                await self._persist_event(app_session_id, "session.error", {"message": str(exc)})
                await self._set_session_status(app_session_id, "error", last_error=str(exc))
                raise
            finally:
                runtime.turn_state = None

    async def disconnect_session(self, app_session_id: str) -> None:
        runtime = self._runtimes.pop(app_session_id, None)
        if not runtime:
            return
        try:
            runtime.unsubscribe()
        except Exception:
            pass
        try:
            await runtime.session.disconnect()
        except Exception:
            logger.debug("Failed to disconnect Copilot session %s", app_session_id, exc_info=True)
        try:
            await runtime.client.stop()
        except Exception:
            logger.debug("Failed to stop Copilot client %s", app_session_id, exc_info=True)

    async def shutdown(self) -> None:
        self._models_cache = None
        for session_id in list(self._runtimes.keys()):
            await self.disconnect_session(session_id)


runtime_manager = ShotwrightCopilotRuntimeManager()
