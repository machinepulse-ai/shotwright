"""Copilot SDK runtime manager for Shotwright chat sessions."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from copilot import CopilotClient, SubprocessConfig
from copilot.session import PermissionHandler

from app.config import settings
from app.database import (
    get_admin_collection,
    get_event_collection,
    get_message_collection,
    get_session_collection,
)
from app.services.agent_tools import build_shotwright_tools

logger = logging.getLogger(__name__)


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

    def track_task(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.pending_tasks.add(task)
        task.add_done_callback(self.pending_tasks.discard)


class ShotwrightCopilotRuntimeManager:
    def __init__(self) -> None:
        self._runtimes: dict[str, _RuntimeHandle] = {}
        self._setup_lock = asyncio.Lock()

    async def _resolve_github_token(self) -> str | None:
        if settings.github_token:
            return settings.github_token
        doc = await get_admin_collection().find_one({"_id": "settings"})
        token = doc.get("github_token") if doc else None
        return token or None

    async def _resolve_runtime_settings(self) -> dict[str, str | bool]:
        doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
        return {
            "copilot_model": _first_non_empty(doc.get("copilot_model"), settings.copilot_model) or "gpt-5.4",
            "copilot_reasoning_effort": _first_non_empty(
                doc.get("copilot_reasoning_effort"),
                settings.copilot_reasoning_effort,
            )
            or "high",
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

    def _system_prompt(self) -> str:
        return (
            "You are Shotwright's After Effects operator. "
            "Your job is to help the user accomplish creative work by using the provided tools only. "
            "Always inspect the workspace state before taking action when the current container, project, or render state is unclear. "
            "Do not claim that an After Effects action happened unless a tool succeeded. "
            "Prefer starting or reusing a container before JSX or render actions. "
            "If multiple uploaded projects or AEP files exist and the intended target is ambiguous, ask the user a concise clarification question. "
            "When rendering succeeds, mention the preview stream and export archive when relevant."
        )

    def _custom_agents(self) -> list[dict]:
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
        return [
            {
                "name": "ae_operator",
                "display_name": "After Effects Operator",
                "description": "Controls Shotwright containers and runs After Effects tasks through guarded backend tools.",
                "tools": tool_names,
                "prompt": self._system_prompt(),
            }
        ]

    async def _persist_event(self, app_session_id: str, event_type: str, data: dict) -> None:
        if event_type in {"assistant.message", "assistant.message_delta", "assistant.reasoning_delta", "user.message"}:
            return
        await get_event_collection().insert_one(
            {
                "_id": str(uuid4()),
                "session_id": app_session_id,
                "type": event_type,
                "summary": _event_summary(event_type, data),
                "data": data,
                "created_at": _utcnow(),
            }
        )

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
        return doc

    async def _set_session_status(self, app_session_id: str, status: str, **extra) -> None:
        await get_session_collection().update_one(
            {"_id": app_session_id},
            {"$set": {"status": status, "updated_at": _utcnow(), **extra}},
        )

    async def _build_runtime(self, app_session_id: str) -> _RuntimeHandle:
        session_doc = await get_session_collection().find_one({"_id": app_session_id})
        if not session_doc:
            raise ValueError("Session not found")

        runtime_settings = await self._resolve_runtime_settings()
        github_token = await self._resolve_github_token()
        if not github_token and not runtime_settings["copilot_use_logged_in_user"]:
            raise ValueError("GitHub token is not configured for Copilot SDK")

        client = CopilotClient(
            SubprocessConfig(
                github_token=github_token,
                cli_path=(runtime_settings["copilot_cli_path"] or None),
                cwd=str(runtime_settings["copilot_workspace_root"]),
                env=self._build_subprocess_env(runtime_settings),
                use_logged_in_user=bool(runtime_settings["copilot_use_logged_in_user"]),
            )
        )
        await client.start()

        copilot_session_id = session_doc.get("copilot_session_id") or f"shotwright-{app_session_id}"
        session_kwargs = {
            "on_permission_request": PermissionHandler.approve_all,
            "model": runtime_settings["copilot_model"],
            "reasoning_effort": runtime_settings["copilot_reasoning_effort"],
            "streaming": True,
            "available_tools": [],
            "tools": build_shotwright_tools(app_session_id),
            "system_message": {"mode": "replace", "content": self._system_prompt()},
            "custom_agents": self._custom_agents(),
            "agent": "ae_operator",
        }

        if session_doc.get("copilot_session_id"):
            session = await client.resume_session(copilot_session_id, **session_kwargs)
        else:
            session = await client.create_session(session_id=copilot_session_id, **session_kwargs)
            await self._set_session_status(app_session_id, session_doc.get("status") or "idle", copilot_session_id=session.session_id)

        def on_event(event):
            data = _serialize(getattr(event, "data", None)) or {}
            runtime = self._runtimes.get(app_session_id)
            if runtime:
                runtime.track_task(self._persist_event(app_session_id, _event_type_name(event), data))

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
            await self._set_session_status(app_session_id, "running", last_error=None)
            try:
                response = await runtime.session.send_and_wait(content)
                assistant_text = ""
                if response and getattr(response, "data", None):
                    assistant_text = getattr(response.data, "content", "") or ""
                assistant_doc = await self._persist_message(app_session_id, "assistant", assistant_text)
                await self._set_session_status(app_session_id, "idle")
                if runtime.pending_tasks:
                    await asyncio.gather(*list(runtime.pending_tasks), return_exceptions=True)
                return {
                    "assistant_message": assistant_doc,
                    "session_status": "idle",
                }
            except Exception as exc:
                logger.exception("Copilot turn failed for %s", app_session_id)
                await self._persist_event(app_session_id, "session.error", {"message": str(exc)})
                await self._set_session_status(app_session_id, "error", last_error=str(exc))
                raise

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
        for session_id in list(self._runtimes.keys()):
            await self.disconnect_session(session_id)


runtime_manager = ShotwrightCopilotRuntimeManager()
