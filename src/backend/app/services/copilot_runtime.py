"""Copilot SDK runtime manager for Shotwright chat sessions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
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
    get_project_collection,
    get_session_collection,
)
from app.models.session import ReasoningEffort
from app.services.agent_tools import build_shotwright_tools
from app.services import nexrender as nr
from app.services.session_streams import (
    publish_context_refresh,
    publish_message_deleted,
    publish_message_upsert,
    publish_session_updated,
    publish_timeline_event,
)

logger = logging.getLogger(__name__)

_IN_MEMORY_MODEL_CACHE_TTL_SECONDS = 60
_MONGO_MODEL_CACHE_TTL_SECONDS = 600
_REPO_SKILLS_HYDRATION_RETRY_INTERVAL_SECONDS = 300
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS_ROOT = _REPO_ROOT / "scripts"
_SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
_INLINE_ATTACHMENT_DIRECTORY = Path("_inline-images")
_INLINE_ATTACHMENT_SUFFIXES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


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
    if event_type == "session.turn.started":
        return "Turn submitted to Copilot runtime"
    if event_type == "session.cancelled":
        return data.get("message") or "Turn cancelled"
    if event_type == "session.timeout":
        timeout_seconds = data.get("timeout_seconds")
        return f"Turn timed out after {timeout_seconds}s" if timeout_seconds else "Turn timed out"
    if event_type == "tool.execution_start":
        return f"Tool start: {data.get('tool_name') or data.get('toolName') or 'unknown'}"
    if event_type == "tool.execution_complete":
        tool_name = data.get("tool_name") or data.get("toolName") or "unknown"
        success = data.get("success")
        if success is True:
            outcome = "ok"
        elif success is False:
            outcome = "failed"
        else:
            outcome = "completed"
        return f"Tool complete: {tool_name} ({outcome})"
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


def _clear_current_task_cancellation() -> None:
    current_task = asyncio.current_task()
    if current_task is None:
        return

    cancelling = getattr(current_task, "cancelling", None)
    uncancel = getattr(current_task, "uncancel", None)
    if not callable(cancelling) or not callable(uncancel):
        return

    while current_task.cancelling():
        current_task.uncancel()


def _load_skills_bundle_helpers():
    module_name = "shotwright_skills_bundle"
    existing_module = sys.modules.get(module_name)
    if existing_module is not None:
        return existing_module.SkillsBundleError, existing_module.ensure_skills_bundle

    spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS_ROOT / "skills_bundle.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load skills bundle helpers from {_SCRIPTS_ROOT / 'skills_bundle.py'}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.SkillsBundleError, module.ensure_skills_bundle


class TurnCancelledError(Exception):
    """Raised when a user manually stops an in-flight Copilot turn."""


def _inline_attachment_file_name(display_name: str, mime_type: str, payload: bytes) -> str:
    raw_name = Path(display_name).name if display_name else ""
    fallback_stem = f"image-{hashlib.sha256(payload).hexdigest()[:12]}"
    raw_stem = Path(raw_name).stem.strip() if raw_name else ""
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in raw_stem)
    safe_stem = safe_stem.strip(" .-") or fallback_stem

    raw_suffix = Path(raw_name).suffix.lower() if raw_name else ""
    fallback_suffix = _INLINE_ATTACHMENT_SUFFIXES.get(mime_type.lower(), ".bin")
    safe_suffix = raw_suffix if raw_suffix else fallback_suffix
    if not safe_suffix.startswith("."):
        safe_suffix = f".{safe_suffix}"
    return f"{safe_stem}{safe_suffix}"


def _inline_attachment_storage_dir(app_session_id: str, storage_root: str) -> Path:
    return Path(storage_root) / app_session_id / _INLINE_ATTACHMENT_DIRECTORY


def _save_inline_attachment(
    encoded_payload: str,
    *,
    mime_type: str,
    display_name: str,
    app_session_id: str,
    storage_root: str,
) -> tuple[Path, str] | None:
    try:
        payload = base64.b64decode(encoded_payload, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("Skipping undecodable inline image attachment for session %s", app_session_id)
        return None

    storage_path = Path(storage_root)
    storage_dir = _inline_attachment_storage_dir(app_session_id, storage_root)
    storage_dir.mkdir(parents=True, exist_ok=True)

    file_name = _inline_attachment_file_name(display_name, mime_type, payload)
    file_path = storage_dir / file_name
    file_path.write_bytes(payload)

    try:
        relative_path = file_path.relative_to(storage_path).as_posix()
    except ValueError:
        relative_path = file_path.name
    return file_path, relative_path


def _prepare_turn_attachments(
    attachments: list[dict] | None,
    *,
    app_session_id: str | None = None,
    storage_root: str | None = None,
) -> tuple[list[dict], list[dict]]:
    copilot_attachments: list[dict] = []
    persisted_attachments: list[dict] = []

    for attachment in attachments or []:
        data_url = str(attachment.get("data_url") or "")
        _, _, encoded_payload = data_url.partition(",")
        if not encoded_payload:
            if app_session_id:
                logger.warning("Skipping malformed inline image attachment for session %s", app_session_id)
            continue

        mime_type = str(attachment.get("mime_type") or "application/octet-stream")
        display_name = _first_non_empty(attachment.get("display_name"))

        copilot_attachment = {
            "type": "blob",
            "data": encoded_payload,
            "mimeType": mime_type,
        }
        copilot_attachments.append(copilot_attachment)

        persisted_attachment = {
            "type": "image",
            "mime_type": mime_type,
            "data_url": data_url,
        }

        if app_session_id and storage_root:
            saved_attachment = _save_inline_attachment(
                encoded_payload,
                mime_type=mime_type,
                display_name=display_name,
                app_session_id=app_session_id,
                storage_root=storage_root,
            )
            if saved_attachment:
                file_path, relative_path = saved_attachment
                file_attachment = {
                    "type": "file",
                    "path": str(file_path),
                }
                if file_path.name:
                    file_attachment["displayName"] = file_path.name
                copilot_attachments.append(file_attachment)
                persisted_attachment["file_path"] = str(file_path)
                persisted_attachment["shared_relative_path"] = relative_path
                persisted_attachment["workspace_relative_path"] = relative_path

        for key in ("display_name", "width", "height", "size_bytes"):
            value = attachment.get(key)
            if value not in (None, ""):
                persisted_attachment[key] = value
        persisted_attachments.append(persisted_attachment)

    return copilot_attachments, persisted_attachments


class _RuntimeHandle:
    def __init__(self, app_session_id: str, client: CopilotClient, session, unsubscribe, workspace_root: str) -> None:
        self.app_session_id = app_session_id
        self.client = client
        self.session = session
        self.unsubscribe = unsubscribe
        self.workspace_root = workspace_root
        self.lock = asyncio.Lock()
        self.pending_tasks: set[asyncio.Task] = set()
        self.turn_state: _StreamingTurnState | None = None
        self.event_sequence = 0

    def track_task(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.pending_tasks.add(task)
        task.add_done_callback(self.pending_tasks.discard)

    def next_event_sequence(self) -> int:
        self.event_sequence += 1
        return self.event_sequence


class _StreamingTurnState:
    def __init__(self, message_id: str, *, turn_id: str, user_message_id: str) -> None:
        self.message_id = message_id
        self.turn_id = turn_id
        self.user_message_id = user_message_id
        self.content = ""
        self.version = 0
        self.finalized = False
        self.idle_event = asyncio.Event()
        self.error: Exception | None = None
        self.error_event_seen = False
        self.cancel_requested = False
        self.request_task: asyncio.Task | None = None


class _PendingTurnBootstrap:
    def __init__(
        self,
        *,
        turn_id: str,
        request_task: asyncio.Task | None,
        persisted_attachments: list[dict],
    ) -> None:
        self.turn_id = turn_id
        self.request_task = request_task
        self.persisted_attachments = persisted_attachments
        self.cancel_requested = False
        self.cancellation_message = "Generation stopped by user."


class ShotwrightCopilotRuntimeManager:
    def __init__(self) -> None:
        self._runtimes: dict[str, _RuntimeHandle] = {}
        self._pending_turn_bootstraps: dict[str, _PendingTurnBootstrap] = {}
        self._repo_skills_hydration_task: asyncio.Task | None = None
        self._repo_skills_hydration_last_attempt_at = 0.0
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
        configured_workspace_root = (
            _first_non_empty(
                doc.get("copilot_workspace_root"),
                settings.copilot_workspace_root,
            )
            or "C:\\workspace"
        )
        return {
            "copilot_cli_path": _first_non_empty(doc.get("copilot_cli_path"), settings.copilot_cli_path),
            "copilot_workspace_root": self._resolve_workspace_root(configured_workspace_root),
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

    async def _ensure_repo_skill_bundle(
        self,
        runtime_settings: dict[str, str | bool],
        github_token: str | None,
    ) -> None:
        proxy = _first_non_empty(
            str(runtime_settings.get("copilot_https_proxy") or ""),
            str(runtime_settings.get("copilot_http_proxy") or ""),
        ) or None
        skills_bundle_error, ensure_bundle = _load_skills_bundle_helpers()
        try:
            result = await asyncio.to_thread(
                ensure_bundle,
                source_repo_root=_REPO_ROOT,
                install_root=_REPO_ROOT,
                proxy=proxy,
                github_token=github_token,
                log=logger.info,
            )
        except skills_bundle_error as exc:
            raise ValueError(f"Shotwright skills bundle is unavailable: {exc}") from exc

        if result.get("status") == "downloaded":
            logger.info(
                "Hydrated Shotwright skills bundle %s into %s",
                result.get("artifactVersion"),
                result.get("skillsRoot"),
            )

    async def _hydrate_repo_skill_bundle_in_background(
        self,
        runtime_settings: dict[str, str | bool],
        github_token: str | None,
    ) -> None:
        try:
            await self._ensure_repo_skill_bundle(runtime_settings, github_token)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Continuing without Shotwright repo skills because background hydration failed",
                exc_info=True,
            )
        finally:
            current_task = asyncio.current_task()
            if self._repo_skills_hydration_task is current_task:
                self._repo_skills_hydration_task = None

    def _prime_repo_skill_bundle(
        self,
        runtime_settings: dict[str, str | bool],
        github_token: str | None,
    ) -> None:
        if self._resolve_skill_names(self._resolve_skill_directories(runtime_settings)):
            return

        existing_task = self._repo_skills_hydration_task
        if existing_task is not None and existing_task.done():
            self._repo_skills_hydration_task = None
            existing_task = None
        if existing_task is not None:
            return

        if (
            monotonic() - self._repo_skills_hydration_last_attempt_at
            < _REPO_SKILLS_HYDRATION_RETRY_INTERVAL_SECONDS
        ):
            return

        self._repo_skills_hydration_last_attempt_at = monotonic()
        logger.info(
            "Shotwright repo skills are not available locally yet; starting background hydration"
        )
        self._repo_skills_hydration_task = asyncio.create_task(
            self._hydrate_repo_skill_bundle_in_background(runtime_settings, github_token)
        )

    async def ensure_repo_skills_available(self) -> None:
        runtime_settings = await self._resolve_runtime_settings()
        github_token = await self._resolve_github_token()
        await self._ensure_repo_skill_bundle(runtime_settings, github_token)

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

    def _workspace_root_candidates(self, configured_workspace_root: str | None) -> list[str]:
        candidates: list[str] = []
        for candidate in (
            configured_workspace_root,
            settings.copilot_workspace_root,
            os.environ.get("SHOTWRIGHT_REPO_ROOT"),
            os.getcwd(),
            str(_REPO_ROOT),
        ):
            normalized = _first_non_empty(candidate)
            if not normalized:
                continue
            resolved = os.path.abspath(normalized)
            if resolved not in candidates:
                candidates.append(resolved)
        return candidates

    def _resolve_workspace_root(self, configured_workspace_root: str) -> str:
        configured_root = os.path.abspath(configured_workspace_root)
        for candidate in self._workspace_root_candidates(configured_workspace_root):
            if os.path.isdir(candidate):
                if candidate != configured_root:
                    logger.warning(
                        "Configured Copilot workspace root %s is unavailable; falling back to %s",
                        configured_workspace_root,
                        candidate,
                    )
                return candidate

        return configured_root

    async def get_runtime_settings(self) -> dict[str, str | bool]:
        return await self._resolve_runtime_settings()

    def _normalize_turn_timeout_seconds(self, value: object | None) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return settings.copilot_turn_timeout_seconds
        return normalized if normalized > 0 else settings.copilot_turn_timeout_seconds

    async def resolve_turn_timeout_seconds(self) -> float:
        doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
        return self._normalize_turn_timeout_seconds(doc.get("copilot_turn_timeout_seconds"))

    async def resolve_default_session_settings(self) -> tuple[str, ReasoningEffort | None]:
        doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
        configured_model = _first_non_empty(doc.get("default_copilot_model"), settings.copilot_model) or settings.copilot_model
        configured_reasoning_effort = (
            doc["default_copilot_reasoning_effort"]
            if "default_copilot_reasoning_effort" in doc
            else settings.copilot_reasoning_effort
        )

        # Do not block hot request paths such as session creation on a live model
        # enumeration round-trip. If the Copilot CLI is slow or unhealthy, model
        # validation here can make the whole API appear down. Explicit model edits
        # still validate through validate_model_choice().
        normalized_reasoning_effort = (
            configured_reasoning_effort
            if configured_reasoning_effort in _SUPPORTED_REASONING_EFFORTS
            else settings.copilot_reasoning_effort
        )
        if configured_reasoning_effort and configured_reasoning_effort not in _SUPPORTED_REASONING_EFFORTS:
            logger.warning(
                "Ignoring unsupported default Copilot reasoning effort %s and falling back to %s",
                configured_reasoning_effort,
                settings.copilot_reasoning_effort,
            )

        return configured_model.strip(), normalized_reasoning_effort

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
            "Your job is to help the user accomplish creative work by using Shotwright's provided tools for concrete runtime actions. "
            "When you generate brief intent labels, short progress headings, or other assistant-authored summaries, use the same language as the user's latest request unless they explicitly ask for a different language. "
            "Relevant repository skills loaded from the workspace are part of your operating instructions, not separate tools, and should be applied whenever they match the task. "
            "When the user explicitly mentions a skill or asks about a skill-defined workflow, answer directly from that skill before considering workspace inspection. "
            "Inspect the workspace at most once at the start of a normal render workflow unless the session state changes or is genuinely unclear. "
            "Do not claim that an After Effects action happened unless a tool succeeded. "
            "Prefer starting or reusing a container before JSX or render actions. "
            "For a blank project, prefer create_empty_after_effects_project instead of handwritten boilerplate JSX. "
            "For user-supplied inline images, prefer inspect_workspace to discover recent image attachments, then use stage_reference_images or create_reference_composition instead of copying files with shell commands. "
            "For user-supplied reference videos, prefer inspect_workspace to discover uploaded reference_videos, then use generate_storyboard_from_reference_video before creating the AEP composition. When only a local motion detail matters, pass the storyboard tool's crop parameter so you can inspect a focused region instead of the whole frame. When comparing against a Shotwright render, pass the session's latest_render_path or another session-local export mp4 into the same storyboard tool so the crop and cadence stay comparable. Inspect workspace state before multi-round edits so you can reuse the stored project compositions and structured render_outputs instead of guessing which comp or mp4 is the latest. "
            "Once a session already has an active project, treat it as the default target for later creative turns. If the user asks to change, add, remove, tweak, or render something without explicitly asking for a new project or a different uploaded archive, keep editing that active project and its current compositions instead of creating another project. "
            "If a project bootstrap tool returns a project_id but the save step fails, keep using that same managed workspace on the next retry instead of creating another project. "
            "If you need to inspect a generated storyboard visually, use the available file or image viewing tool on the storyboard path returned by the Shotwright tool. "
            "Use run_after_effects_jsx only for creative edits that are not already covered by the higher-level Shotwright tools. "
            "If JSX execution fails, retry at most once on the same project workspace or ask the user a concise clarification question; do not switch into repository inspection, shell-driven recovery, or brand-new project creation during a normal creative turn. "
            "For normal Shotwright creative work, do not use powershell, read_powershell, list_powershell, read_agent, list_agents, task, subagents, or glob unless the user explicitly asks for repository inspection or every relevant higher-level Shotwright tool has already failed to perform the required action. "
            "Do not override the container image unless the user explicitly asks for a different image. "
            "If the user asks to create a new AEP and no suitable project already exists, create a managed Shotwright project workspace first and save the .aep there before rendering or exporting. "
            "If multiple uploaded projects or AEP files exist and the intended target is ambiguous, ask the user a concise clarification question. "
            "When rendering succeeds, mention the preview stream and export archive when relevant."
        )

    async def _build_runtime_turn_content(self, app_session_id: str, content: str) -> str:
        try:
            session_collection = get_session_collection()
            project_collection = get_project_collection()
        except AssertionError:
            return content

        session_doc = await session_collection.find_one({"_id": app_session_id})
        if not session_doc:
            return content

        active_project_id = str(session_doc.get("active_project_id") or "").strip()
        if not active_project_id:
            return content

        project_doc = await project_collection.find_one({"_id": active_project_id, "session_id": app_session_id})
        if not project_doc:
            return content

        project_name = _first_non_empty(
            str(project_doc.get("entry_aep_file") or ""),
            str(project_doc.get("filename") or ""),
            active_project_id,
        )
        compositions = [
            str(item.get("name") or "").strip()
            for item in (project_doc.get("compositions") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        recent_render_outputs = [
            str(item.get("filename") or "").strip()
            for item in nr.list_render_outputs(app_session_id, limit=3)
            if str(item.get("filename") or "").strip()
        ]

        preamble_lines = [
            "Shotwright session context:",
            f"- Active project id: {active_project_id}",
            f"- Active project file: {project_name}",
        ]
        if compositions:
            preamble_lines.append(f"- Known compositions: {', '.join(compositions)}")
        if recent_render_outputs:
            preamble_lines.append(f"- Recent render outputs: {', '.join(recent_render_outputs)}")
        preamble_lines.extend(
            [
                "Default behavior for this turn:",
                "- Reuse the active project and its current compositions unless the user explicitly asks to create or switch projects.",
                "- Treat follow-up requests like change, add, remove, tweak, update, or render again as edits to this active project.",
                "- Do not create another project for a normal follow-up render or visual adjustment.",
                "",
                "User request:",
                content,
            ]
        )
        return "\n".join(preamble_lines)

    def _custom_agents(self, skill_names: list[str]) -> list[dict]:
        tool_names = [
            "inspect_workspace",
            "ensure_after_effects_container",
            "create_after_effects_project",
            "create_empty_after_effects_project",
            "list_uploaded_projects",
            "select_active_project",
            "generate_storyboard_from_reference_video",
            "stage_reference_images",
            "create_reference_composition",
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
        directories: list[str] = []
        seen: set[str] = set()

        for workspace_root in self._workspace_root_candidates(str(runtime_settings["copilot_workspace_root"])):
            for path in (
                os.path.join(workspace_root, ".github", "skills"),
                os.path.join(workspace_root, ".agents", "skills"),
                os.path.join(workspace_root, ".claude", "skills"),
            ):
                if path in seen or not os.path.isdir(path):
                    continue
                seen.add(path)
                directories.append(path)

        return directories

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

    async def _load_last_event_sequence(self, app_session_id: str) -> int:
        latest_event = await get_event_collection().find_one(
            {
                "session_id": app_session_id,
                "sequence": {"$type": "number"},
            },
            sort=[("sequence", -1), ("created_at", -1)],
        )
        sequence = latest_event.get("sequence") if latest_event else 0
        return sequence if isinstance(sequence, int) and sequence > 0 else 0

    async def _persist_event(
        self,
        app_session_id: str,
        event_type: str,
        data: dict,
        *,
        turn_id: str | None = None,
        sequence: int | None = None,
    ) -> None:
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
            "turn_id": turn_id,
            "sequence": sequence,
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
                    "metadata.streaming": streaming,
                    "metadata.state": state,
                    "metadata.version": version,
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

    async def reconcile_session_status(self, app_session_id: str, session_doc: dict | None = None) -> dict | None:
        if session_doc is None:
            session_doc = await get_session_collection().find_one({"_id": app_session_id})
        if not session_doc or session_doc.get("status") != "running":
            return session_doc

        runtime = self._runtimes.get(app_session_id)
        if runtime and runtime.lock.locked():
            return session_doc

        turn_state = runtime.turn_state if runtime else None
        if turn_state and not turn_state.finalized:
            return session_doc

        error_message = (
            str(session_doc.get("last_error") or "").strip()
            or "Shotwright lost the active Copilot turn state. The previous run likely failed or disconnected."
        )

        if runtime:
            await self.disconnect_session(app_session_id)

        updated_session = await self._set_session_status(
            app_session_id,
            "error",
            last_error=error_message,
        )
        return updated_session or session_doc

    async def _build_runtime(self, app_session_id: str) -> _RuntimeHandle:
        session_doc = await get_session_collection().find_one({"_id": app_session_id})
        if not session_doc:
            raise ValueError("Session not found")

        runtime_settings = await self._resolve_runtime_settings()
        github_token = await self._resolve_github_token()
        if not github_token and not runtime_settings["copilot_use_logged_in_user"]:
            raise ValueError("GitHub token is not configured for Copilot SDK")
        self._prime_repo_skill_bundle(runtime_settings, github_token)

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
                sequence = runtime.next_event_sequence()
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
                    if turn_state.finalized:
                        return
                    final_content = getattr(event_data, "content", None)
                    if isinstance(final_content, str) and final_content:
                        turn_state.content = final_content
                    elif not turn_state.content.strip():
                        return
                    turn_state.finalized = True
                    turn_state.idle_event.set()
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

                if turn_state and event_type == "session.error":
                    message = data.get("message") or data.get("error") or str(event_data)
                    turn_state.error = Exception(str(message))
                    turn_state.error_event_seen = True
                    turn_state.idle_event.set()

                if turn_state and event_type == "session.idle":
                    turn_state.idle_event.set()

                runtime.track_task(
                    self._persist_event(
                        app_session_id,
                        event_type,
                        data,
                        turn_id=turn_state.turn_id if turn_state else None,
                        sequence=sequence,
                    )
                )

        unsubscribe = session.on(on_event)
        handle = _RuntimeHandle(
            app_session_id,
            client,
            session,
            unsubscribe,
            str(runtime_settings["copilot_workspace_root"]),
        )
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
            runtime.event_sequence = await self._load_last_event_sequence(app_session_id)
            self._runtimes[app_session_id] = runtime
            runtime.track_task(
                self._persist_event(
                    app_session_id,
                    "session.created",
                    {"copilot_session_id": runtime.session.session_id},
                    sequence=runtime.next_event_sequence(),
                )
            )
            return runtime

    async def send_message(self, app_session_id: str, content: str, attachments: list[dict] | None = None) -> dict:
        turn_id = str(uuid4())
        turn_timeout_seconds = await self.resolve_turn_timeout_seconds()
        copilot_attachments, persisted_attachments = _prepare_turn_attachments(
            attachments,
            app_session_id=app_session_id,
            storage_root=settings.upload_dir,
        )
        pending_bootstrap = _PendingTurnBootstrap(
            turn_id=turn_id,
            request_task=asyncio.current_task(),
            persisted_attachments=persisted_attachments,
        )
        self._pending_turn_bootstraps[app_session_id] = pending_bootstrap
        await self._set_session_status(app_session_id, "running", last_error=None)

        async def _finish_bootstrap_terminal_state(
            *,
            assistant_content: str,
            assistant_state: str,
            session_status: str,
            event_type: str,
            event_payload: dict,
            last_error: str | None,
        ) -> dict:
            user_metadata = {"turn_id": turn_id, "kind": "user_prompt"}
            if persisted_attachments:
                user_metadata["attachments"] = persisted_attachments
            await self._persist_message(
                app_session_id,
                "user",
                content,
                user_metadata,
            )

            image_attachment_count = sum(
                1
                for attachment in persisted_attachments
                if isinstance(attachment, dict)
                and attachment.get("type") == "image"
                and attachment.get("shared_relative_path")
            )
            if image_attachment_count:
                await publish_context_refresh(
                    app_session_id,
                    "attachments.updated",
                    image_attachment_count=image_attachment_count,
                )

            assistant_doc = await self._persist_message(
                app_session_id,
                "assistant",
                assistant_content,
                {
                    "turn_id": turn_id,
                    "kind": "assistant_reply",
                    "streaming": False,
                    "state": assistant_state,
                    "version": 1,
                },
            )
            await self._persist_event(
                app_session_id,
                event_type,
                event_payload,
                turn_id=turn_id,
            )
            await self._set_session_status(app_session_id, session_status, last_error=last_error)
            return {
                "assistant_message": assistant_doc,
                "session_status": session_status,
            }

        try:
            runtime = await self.ensure_runtime(app_session_id)
        except asyncio.CancelledError:
            if pending_bootstrap.cancel_requested:
                _clear_current_task_cancellation()

                return await _finish_bootstrap_terminal_state(
                    assistant_content=pending_bootstrap.cancellation_message,
                    assistant_state="cancelled",
                    session_status="idle",
                    event_type="session.cancelled",
                    event_payload={
                        "message": pending_bootstrap.cancellation_message,
                        "partial_output": False,
                    },
                    last_error=None,
                )

            interruption_message = (
                "Shotwright interrupted this turn because the API worker restarted or the request disconnected."
            )
            logger.warning(
                "Copilot turn interrupted for %s during runtime setup",
                app_session_id,
            )
            _clear_current_task_cancellation()
            return await _finish_bootstrap_terminal_state(
                assistant_content=interruption_message,
                assistant_state="error",
                session_status="error",
                event_type="session.error",
                event_payload={
                    "message": interruption_message,
                    "partial_output": False,
                    "interrupted": True,
                },
                last_error=interruption_message,
            )
        except Exception as exc:
            error_message = str(exc).strip() or exc.__class__.__name__
            logger.exception("Copilot turn failed for %s during runtime setup", app_session_id)
            return await _finish_bootstrap_terminal_state(
                assistant_content=error_message,
                assistant_state="error",
                session_status="error",
                event_type="session.error",
                event_payload={"message": error_message},
                last_error=error_message,
            )

        try:
            async with runtime.lock:
                user_metadata = {"turn_id": turn_id, "kind": "user_prompt"}
                if persisted_attachments:
                    user_metadata["attachments"] = persisted_attachments
                user_doc = await self._persist_message(
                    app_session_id,
                    "user",
                    content,
                    user_metadata,
                )
                image_attachment_count = sum(
                    1
                    for attachment in persisted_attachments
                    if isinstance(attachment, dict)
                    and attachment.get("type") == "image"
                    and attachment.get("shared_relative_path")
                )
                if image_attachment_count:
                    await publish_context_refresh(
                        app_session_id,
                        "attachments.updated",
                        image_attachment_count=image_attachment_count,
                    )
                assistant_doc = await self._persist_message(
                    app_session_id,
                    "assistant",
                    "",
                    {
                        "turn_id": turn_id,
                        "kind": "assistant_reply",
                        "streaming": True,
                        "state": "pending",
                        "version": 0,
                    },
                )
                runtime.turn_state = _StreamingTurnState(
                    assistant_doc["_id"],
                    turn_id=turn_id,
                    user_message_id=user_doc["_id"],
                )
                runtime.turn_state.request_task = asyncio.current_task()
                if self._pending_turn_bootstraps.get(app_session_id) is pending_bootstrap:
                    self._pending_turn_bootstraps.pop(app_session_id, None)

                async def _finish_cancelled_turn(cancellation_message: str) -> dict:
                    nonlocal assistant_doc

                    partial_output = bool(runtime.turn_state and runtime.turn_state.content.strip())

                    if runtime.turn_state:
                        runtime.turn_state.finalized = True
                        runtime.turn_state.version += 1
                        updated_doc = await self._sync_streaming_message(
                            runtime.turn_state,
                            content=runtime.turn_state.content if partial_output else cancellation_message,
                            version=runtime.turn_state.version,
                            streaming=False,
                            state="cancelled",
                        )
                        if updated_doc:
                            assistant_doc = updated_doc

                    await self._persist_event(
                        app_session_id,
                        "session.cancelled",
                        {
                            "message": cancellation_message,
                            "partial_output": partial_output,
                        },
                        turn_id=turn_id,
                        sequence=runtime.next_event_sequence(),
                    )
                    await self._set_session_status(app_session_id, "idle", last_error=None)

                    if runtime.pending_tasks:
                        await asyncio.gather(*list(runtime.pending_tasks), return_exceptions=True)
                    await self.disconnect_session(app_session_id)

                    if runtime.turn_state:
                        assistant_doc = await get_message_collection().find_one({"_id": runtime.turn_state.message_id}) or assistant_doc

                    return {
                        "assistant_message": assistant_doc,
                        "session_status": "idle",
                    }

                try:
                    runtime_content = await self._build_runtime_turn_content(app_session_id, content)
                    copilot_message_id = await runtime.session.send(
                        runtime_content,
                        attachments=copilot_attachments or None,
                    )
                    await self._persist_event(
                        app_session_id,
                        "session.turn.started",
                        {
                            "attachment_count": len(persisted_attachments),
                            "attachment_mime_types": [attachment.get("mime_type") for attachment in persisted_attachments],
                            "copilot_message_id": copilot_message_id,
                            "timeout_seconds": turn_timeout_seconds,
                        },
                        turn_id=turn_id,
                        sequence=runtime.next_event_sequence(),
                    )
                    await asyncio.wait_for(
                        runtime.turn_state.idle_event.wait(),
                        timeout=turn_timeout_seconds,
                    )
                    if runtime.turn_state.error:
                        raise runtime.turn_state.error

                    turn_state = runtime.turn_state
                    assistant_text = turn_state.content if turn_state else ""
                    if turn_state and not assistant_text.strip():
                        assistant_text = "Shotwright completed the requested work. Inspect the updated session state for the active project, renders, or other artifacts."

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
                except TurnCancelledError as exc:
                    cancellation_message = str(exc).strip() or "Generation stopped by user."
                    return await _finish_cancelled_turn(cancellation_message)
                except asyncio.CancelledError:
                    if runtime.turn_state and runtime.turn_state.cancel_requested:
                        _clear_current_task_cancellation()
                        cancellation_message = (
                            str(runtime.turn_state.error).strip()
                            if runtime.turn_state.error
                            else "Generation stopped by user."
                        )
                        return await _finish_cancelled_turn(cancellation_message)

                    interruption_message = (
                        "Shotwright interrupted this turn because the API worker restarted or the request disconnected."
                    )
                    logger.warning(
                        "Copilot turn interrupted for %s while waiting for completion",
                        app_session_id,
                    )
                    _clear_current_task_cancellation()

                    partial_output = bool(runtime.turn_state and runtime.turn_state.content.strip())
                    if runtime.turn_state:
                        runtime.turn_state.error = Exception(interruption_message)
                        runtime.turn_state.error_event_seen = True
                        runtime.turn_state.finalized = True
                        runtime.turn_state.version += 1
                        updated_doc = await self._sync_streaming_message(
                            runtime.turn_state,
                            content=runtime.turn_state.content if partial_output else interruption_message,
                            version=runtime.turn_state.version,
                            streaming=False,
                            state="error",
                        )
                        if updated_doc:
                            assistant_doc = updated_doc

                    await self._persist_event(
                        app_session_id,
                        "session.error",
                        {
                            "message": interruption_message,
                            "partial_output": partial_output,
                            "interrupted": True,
                        },
                        turn_id=turn_id,
                        sequence=runtime.next_event_sequence(),
                    )
                    await self._set_session_status(app_session_id, "error", last_error=interruption_message)

                    if runtime.pending_tasks:
                        await asyncio.gather(*list(runtime.pending_tasks), return_exceptions=True)
                    await self.disconnect_session(app_session_id)

                    if runtime.turn_state:
                        assistant_doc = await get_message_collection().find_one({"_id": runtime.turn_state.message_id}) or assistant_doc

                    return {
                        "assistant_message": assistant_doc,
                        "session_status": "error",
                    }
                except TimeoutError:
                    timeout_message = (
                        f"Shotwright timed out waiting for this turn after {turn_timeout_seconds:g} seconds."
                    )
                    logger.warning(
                        "Copilot turn timed out for %s after %ss",
                        app_session_id,
                        turn_timeout_seconds,
                    )

                    partial_output = bool(runtime.turn_state and runtime.turn_state.content.strip())
                    if runtime.turn_state:
                        runtime.turn_state.finalized = True
                        runtime.turn_state.version += 1
                        updated_doc = await self._sync_streaming_message(
                            runtime.turn_state,
                            content=runtime.turn_state.content if partial_output else timeout_message,
                            version=runtime.turn_state.version,
                            streaming=False,
                            state="error",
                        )
                        if updated_doc:
                            assistant_doc = updated_doc

                    await self._persist_event(
                        app_session_id,
                        "session.timeout",
                        {
                            "message": timeout_message,
                            "timeout_seconds": turn_timeout_seconds,
                            "partial_output": partial_output,
                        },
                        turn_id=turn_id,
                        sequence=runtime.next_event_sequence(),
                    )
                    await self._set_session_status(app_session_id, "error", last_error=timeout_message)

                    if runtime.pending_tasks:
                        await asyncio.gather(*list(runtime.pending_tasks), return_exceptions=True)
                    await self.disconnect_session(app_session_id)

                    if runtime.turn_state:
                        assistant_doc = await get_message_collection().find_one({"_id": runtime.turn_state.message_id}) or assistant_doc

                    return {
                        "assistant_message": assistant_doc,
                        "session_status": "error",
                    }
                except Exception as exc:
                    logger.exception("Copilot turn failed for %s", app_session_id)
                    error_message = str(exc).strip() or exc.__class__.__name__
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
                    if not (runtime.turn_state and runtime.turn_state.error_event_seen):
                        await self._persist_event(
                            app_session_id,
                            "session.error",
                            {"message": error_message},
                            turn_id=turn_id,
                            sequence=runtime.next_event_sequence(),
                        )
                    await self._set_session_status(app_session_id, "error", last_error=error_message)
                    raise
                finally:
                    runtime.turn_state = None
        finally:
            if self._pending_turn_bootstraps.get(app_session_id) is pending_bootstrap:
                self._pending_turn_bootstraps.pop(app_session_id, None)

    async def cancel_turn(self, app_session_id: str) -> bool:
        runtime = self._runtimes.get(app_session_id)
        if not runtime or not runtime.turn_state:
            pending_bootstrap = self._pending_turn_bootstraps.get(app_session_id)
            if pending_bootstrap:
                request_task = pending_bootstrap.request_task
                if request_task and request_task is not asyncio.current_task() and not request_task.done():
                    pending_bootstrap.cancel_requested = True
                    request_task.cancel()
                    return True
            await self.reconcile_session_status(app_session_id)
            return False

        turn_state = runtime.turn_state
        if turn_state.finalized:
            await self.reconcile_session_status(app_session_id)
            return False

        turn_state.finalized = True
        turn_state.cancel_requested = True
        turn_state.error = TurnCancelledError("Generation stopped by user.")
        turn_state.idle_event.set()
        request_task = turn_state.request_task
        if request_task and request_task is not asyncio.current_task() and not request_task.done():
            request_task.cancel()
        return True

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
