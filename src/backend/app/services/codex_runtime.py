"""Codex SDK runtime provider for Shotwright chat sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pymongo import ReturnDocument

from app.config import settings
from app.database import (
    get_admin_collection,
    get_event_collection,
    get_message_collection,
    get_project_collection,
    get_session_collection,
)
from app.models.session import ReasoningEffort
from app.services import nexrender as nr
from app.services import reference_media as rm
from app.services.agent_model_metadata import build_agent_model_metadata
from app.services.codex_bridge import CodexBridgeClient, CodexBridgeError, build_codex_input
from app.services.codex_config import (
    build_codex_sdk_config,
    load_local_codex_profile,
    resolve_codex_runtime_settings,
    resolve_codex_runtime_home,
    resolve_openai_api_key,
)
from app.services.copilot_runtime import (
    _SUPPORTED_REASONING_EFFORTS,
    _clear_current_task_cancellation,
    _event_summary,
    _first_non_empty,
    _load_skills_bundle_helpers,
    _prepare_turn_attachments,
    _utcnow,
)
from app.services.codex_tool_runner import build_codex_tool_manifest, run_codex_tool
from app.services.github_token_env import resolve_github_token as resolve_stored_github_token
from app.services.session_streams import (
    publish_context_refresh,
    publish_message_upsert,
    publish_session_updated,
    publish_timeline_event,
)
from app.services.session_titles import maybe_auto_title_session

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TOOL_RUNNER_PATH = _REPO_ROOT / "src" / "backend" / "app" / "services" / "codex_tool_runner.py"
_REPO_SKILL_DIRECTORIES = (".github", "skills"), (".agents", "skills"), (".claude", "skills")
_CODEX_TOOL_COMMAND_RE = re.compile(
    r"(?:codex_tool_runner\.py|app\.services\.codex_tool_runner)",
    flags=re.IGNORECASE,
)
_SHELL_ARG_RE_TEMPLATE = r"--{name}(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))"
_DIRECT_TOOL_MAX_STEPS = 26
_DIRECT_TOOL_THREAD_RECOVERY_RETRIES = 6
_DIRECT_TOOL_TRANSIENT_BRIDGE_RETRIES = 6
_DIRECT_TOOL_TRANSIENT_BRIDGE_RETRY_BASE_DELAY_SECONDS = 2.0
_DIRECT_TOOL_TRANSIENT_BRIDGE_RETRY_MAX_DELAY_SECONDS = 12.0
_DIRECT_TOOL_TIMEOUT_RECOVERY_RETRIES = 2
_DIRECT_TOOL_MODEL_CALL_TIMEOUT_CAP_SECONDS = 180.0
_DIRECT_TOOL_THREAD_RESET_STEP_INTERVAL = 8
_DIRECT_TOOL_THREAD_RESET_INPUT_TOKEN_THRESHOLD = 120_000
_RECENT_CONTEXT_MESSAGE_LIMIT = 6
_RECENT_CONTEXT_MESSAGE_CHAR_LIMIT = 700
_MANGLED_CJK_RANGE_TEXT_RE = re.compile(r"\\x04e00-\\x09fff")
_MANGLED_CJK_RANGE_RE = re.compile(r"\x04e00-\x09fff")
_JSON_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f]")
_DIRECT_TOOL_PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["tool_call", "final"],
            "description": "Use tool_call to request one Shotwright tool execution, or final when the work is complete.",
        },
        "tool_name": {
            "type": "string",
            "description": "Shotwright tool name when action is tool_call; otherwise an empty string.",
        },
        "arguments_json": {
            "type": "string",
            "description": "A JSON object string containing the requested Shotwright tool arguments, or {}.",
        },
        "response": {
            "type": "string",
            "description": "Natural-language final response when action is final; otherwise a short rationale.",
        },
    },
    "required": ["action", "tool_name", "arguments_json", "response"],
    "additionalProperties": False,
}

_DIRECT_TOOL_FOLLOWUP_STRING_LIMIT = 1800
_DIRECT_TOOL_FOLLOWUP_JSON_LIMIT = 12000

_INDEPENDENT_RENDER_REVIEW_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["pass", "fail"],
            "description": "pass only when the rendered storyboard is acceptable to show to the user.",
        },
        "blocking": {
            "type": "boolean",
            "description": "true when the maker agent must revise and render again before finalizing.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Confidence in the visual review verdict.",
        },
        "summary": {
            "type": "string",
            "description": "One concise sentence explaining the verdict.",
        },
        "weakest_frame": {
            "type": "string",
            "description": "The weakest visible frame or region, or an empty string if none stands out.",
        },
        "issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete visual issues found in the attached storyboard images.",
        },
        "revision_brief": {
            "type": "string",
            "description": "Actionable revision instruction for the maker agent when blocking is true.",
        },
    },
    "required": [
        "verdict",
        "blocking",
        "confidence",
        "summary",
        "weakest_frame",
        "issues",
        "revision_brief",
    ],
    "additionalProperties": False,
}


class _CodexRuntimeHandle:
    def __init__(self, app_session_id: str) -> None:
        self.app_session_id = app_session_id
        self.lock = asyncio.Lock()
        self.event_sequence = 0
        self.current_task: asyncio.Task | None = None
        self.cancel_requested = False

    def next_event_sequence(self) -> int:
        self.event_sequence += 1
        return self.event_sequence


class _CodexTurnState:
    def __init__(self, message_id: str, *, turn_id: str) -> None:
        self.message_id = message_id
        self.turn_id = turn_id
        self.content = ""
        self.version = 0
        self.direct_final_response = ""
        self.latest_thread_id: str | None = None
        self.tool_items_started: set[str] = set()
        self.tool_items_completed: set[str] = set()
        self.tool_item_started_at: dict[str, float] = {}


class ShotwrightCodexRuntimeManager:
    def __init__(self) -> None:
        self._runtimes: dict[str, _CodexRuntimeHandle] = {}
        self._setup_lock = asyncio.Lock()

    async def ensure_repo_skills_available(self) -> None:
        runtime_settings = await self._resolve_runtime_settings()
        proxy = _first_non_empty(
            str(runtime_settings.get("codex_https_proxy") or ""),
            str(runtime_settings.get("codex_http_proxy") or ""),
        ) or None
        skills_bundle_error, ensure_bundle = _load_skills_bundle_helpers()
        github_token = await resolve_stored_github_token()
        try:
            await asyncio.to_thread(
                ensure_bundle,
                source_repo_root=_REPO_ROOT,
                install_root=_REPO_ROOT,
                proxy=proxy,
                github_token=github_token,
                log=logger.info,
            )
        except skills_bundle_error as exc:
            raise ValueError(f"Shotwright skills bundle is unavailable: {exc}") from exc

    def _workspace_root_candidates(self, configured_workspace_root: str | None) -> list[str]:
        candidates: list[str] = []
        for candidate in (
            configured_workspace_root,
            settings.codex_workspace_root,
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
                        "Configured Codex workspace root %s is unavailable; falling back to %s",
                        configured_workspace_root,
                        candidate,
                    )
                return candidate
        return configured_root

    async def _resolve_runtime_settings(self) -> dict[str, str | bool | float]:
        doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
        runtime_settings = resolve_codex_runtime_settings(doc)
        runtime_settings["codex_workspace_root"] = self._resolve_workspace_root(
            str(runtime_settings["codex_workspace_root"])
        )
        return runtime_settings

    async def get_runtime_settings(self) -> dict[str, str | bool | float]:
        return await self._resolve_runtime_settings()

    def _resolve_skill_directories(self, runtime_settings: dict[str, str | bool | float]) -> list[str]:
        directories: list[str] = []
        seen: set[str] = set()
        for workspace_root in self._workspace_root_candidates(str(runtime_settings["codex_workspace_root"])):
            for parts in _REPO_SKILL_DIRECTORIES:
                path = os.path.join(workspace_root, *parts)
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

    def _normalize_skill_match_text(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    def _resolve_matching_skill_invocations(
        self,
        runtime_settings: dict[str, str | bool | float],
        content: str,
    ) -> list[dict[str, str]]:
        normalized_content = self._normalize_skill_match_text(content)
        content_lower = content.lower()
        padded_content = f" {normalized_content} "
        after_effects_skill_requested = (
            "skill" in padded_content
            and (
                "after effects" in normalized_content
                or "aftereffects" in normalized_content
                or " ae " in padded_content
            )
        )
        invocations: list[dict[str, str]] = []
        seen_skill_names: set[str] = set()
        for directory in self._resolve_skill_directories(runtime_settings):
            try:
                entries = sorted(
                    (entry for entry in os.scandir(directory) if entry.is_dir()),
                    key=lambda entry: entry.name.lower(),
                )
            except FileNotFoundError:
                continue
            for entry in entries:
                skill_path = os.path.join(entry.path, "SKILL.md")
                if not os.path.isfile(skill_path):
                    continue
                skill_name = entry.name
                normalized_name = self._normalize_skill_match_text(skill_name)
                exact_match = skill_name.lower() in content_lower or (
                    bool(normalized_name) and normalized_name in normalized_content
                )
                ae_skill_match = after_effects_skill_requested and normalized_name == "after effects scripting guide"
                if not exact_match and not ae_skill_match:
                    continue
                canonical_name = normalized_name or skill_name.lower()
                if canonical_name in seen_skill_names:
                    continue
                seen_skill_names.add(canonical_name)
                invocations.append(
                    {
                        "name": skill_name,
                        "path": skill_path,
                        "directory": entry.path,
                    }
                )
        return invocations

    async def resolve_turn_timeout_seconds(self) -> float:
        runtime_settings = await self._resolve_runtime_settings()
        timeout_seconds = runtime_settings.get("codex_turn_timeout_seconds")
        try:
            normalized = float(timeout_seconds)
        except (TypeError, ValueError):
            return settings.codex_turn_timeout_seconds
        return normalized if normalized > 0 else settings.codex_turn_timeout_seconds

    async def resolve_default_session_settings(self) -> tuple[str, ReasoningEffort | None]:
        runtime_settings = await self._resolve_runtime_settings()
        model = _first_non_empty(str(runtime_settings.get("codex_model") or ""), settings.codex_model)
        reasoning_effort = runtime_settings.get("codex_reasoning_effort")
        normalized_reasoning = reasoning_effort if reasoning_effort in _SUPPORTED_REASONING_EFFORTS else None
        return model, normalized_reasoning

    async def list_available_models(self, force_refresh: bool = False) -> list[dict]:
        model, reasoning_effort = await self.resolve_default_session_settings()
        local_profile = load_local_codex_profile()
        model_provider = _first_non_empty(str(local_profile.get("model_provider") or ""))
        supported_efforts = ["low", "medium", "high", "xhigh"]
        return [
            {
                "id": model,
                "name": model,
                **build_agent_model_metadata(
                    model,
                    name=model,
                    provider="codex",
                    model_provider=model_provider or None,
                ),
                "supports_reasoning_effort": True,
                "supported_reasoning_efforts": supported_efforts,
                "default_reasoning_effort": reasoning_effort or "high",
            }
        ]

    async def validate_model_choice(
        self,
        model_id: str,
        reasoning_effort: str | None,
    ) -> tuple[str, ReasoningEffort | None]:
        normalized_model = model_id.strip()
        if not normalized_model:
            raise ValueError("Codex model is required")
        if reasoning_effort is None:
            _, default_reasoning = await self.resolve_default_session_settings()
            return normalized_model, default_reasoning
        if reasoning_effort not in _SUPPORTED_REASONING_EFFORTS:
            raise ValueError(f"Model {normalized_model} does not support reasoning effort {reasoning_effort}")
        return normalized_model, reasoning_effort

    async def apply_session_settings(
        self,
        app_session_id: str,
        model_id: str,
        reasoning_effort: ReasoningEffort | None,
    ) -> None:
        await self._persist_event(
            app_session_id,
            "session.model_updated",
            {"provider": "codex", "model": model_id, "reasoning_effort": reasoning_effort},
        )

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

    async def ensure_runtime(self, app_session_id: str) -> _CodexRuntimeHandle:
        runtime = self._runtimes.get(app_session_id)
        if runtime:
            return runtime

        async with self._setup_lock:
            runtime = self._runtimes.get(app_session_id)
            if runtime:
                return runtime
            runtime = _CodexRuntimeHandle(app_session_id)
            runtime.event_sequence = await self._load_last_event_sequence(app_session_id)
            self._runtimes[app_session_id] = runtime
            return runtime

    async def _persist_event(
        self,
        app_session_id: str,
        event_type: str,
        data: dict,
        *,
        turn_id: str | None = None,
        sequence: int | None = None,
    ) -> None:
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
        turn_state: _CodexTurnState,
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
        if runtime and runtime.current_task and not runtime.current_task.done():
            return session_doc

        error_message = (
            str(session_doc.get("last_error") or "").strip()
            or "Shotwright lost the active Codex turn state. The previous run likely failed or disconnected."
        )
        await self.disconnect_session(app_session_id)
        updated_session = await self._set_session_status(app_session_id, "error", last_error=error_message)
        return updated_session or session_doc

    async def _build_runtime_turn_content(self, app_session_id: str, content: str) -> str:
        runtime_settings = await self._resolve_runtime_settings()
        skill_directories = self._resolve_skill_directories(runtime_settings)
        skill_names = self._resolve_skill_names(skill_directories)
        tool_manifest = build_codex_tool_manifest(app_session_id)
        session_context = await self._build_session_context(app_session_id, content)
        return "\n\n".join(
            [
                self._system_prompt(),
                self._build_skill_bridge_instructions(skill_directories, skill_names),
                self._build_tool_bridge_instructions(app_session_id, tool_manifest),
                session_context,
            ]
        )

    def _system_prompt(self) -> str:
        return "\n".join(
            [
                "Shotwright Codex provider instructions:",
                "- You are Shotwright's After Effects operator inside the repository workspace.",
                "- Your job is to help the user accomplish creative work by using Shotwright's provided tools for concrete runtime actions.",
                "- Keep replies in the same language as the user's latest request unless they ask otherwise.",
                "- Do not claim that an After Effects action happened unless a Shotwright tool, command, script, or render path confirms it.",
                "- Prefer starting or reusing a Shotwright container before JSX or render actions.",
                "- For a blank project, prefer create_empty_after_effects_project instead of handwritten boilerplate JSX.",
                "- For user-supplied inline images, prefer inspect_workspace, then stage_reference_images or create_reference_composition instead of shell file copying.",
                "- For user-supplied reference videos, prefer inspect_workspace, then generate_storyboard_from_reference_video before creating the AEP composition.",
                "- For narration, voiceover, or spoken guide tracks, use generate_tts_audio first, then import the returned project_audio_path into After Effects and align the audio layer with the composition timing.",
                "- Use run_python_code for CPU-only media analysis, synthetic asset generation, audio/video preprocessing, Whisper-style speech analysis, ONNX/InsightFace helpers, and data-driven AE inputs before writing complex JSX.",
                "- In After Effects JSX, create captions, subtitles, title cards, and dense CJK text with comp.layers.addBoxText(); never assign TextDocument.boxText because it is read-only. After setting Source Text, read sourceRectAtTime(0, false), set Anchor Point to [rect.left + rect.width / 2, rect.top + rect.height / 2], and only then position the layer at the intended visual center.",
                "- For Chinese text in After Effects, never set fonts by UI display names like Microsoft YaHei. Use verified PostScript names from inspect_workspace.recommended_fonts or app.fonts.allFonts. Prefer NotoSansSC-Bold/Medium/Regular for readable subtitles, LXGWWenKai-Medium/Regular for cute pet sticker text, and NotoSerifSC-Bold/Medium for title cards. Do not use MS-Gothic/YuGothic for Chinese.",
                "- After setting a TextDocument font, verify textProp.value.font is the intended PostScript name; if AE falls back, immediately switch to the next recommended font before rendering.",
                "- For creative subtitle, title, sticker, transition, and motion design, follow inspect_workspace.creative_quality_policy and inspect_workspace.subtitle_style_policy. These are not fixed style recipes: infer an art direction from the user's goal and source media, then make typography, color, motion, and layout serve that idea.",
                "- Build the first render to a production-facing quality bar: clear creative intent, strong composition, deliberate typography, varied motion rhythm, coherent color and lighting, no generic one-note templates, no random transition spam, no oversized text that can clip or miss glyphs, and no result that is merely technically valid but visually weak.",
                "- For TVC, social video, narration, lyric, subtitle, or reference-driven edits, use run_python_code or generate_storyboard_from_reference_video to inspect media timing before JSX when that improves alignment.",
                "- After every successful render_after_effects_project call, generate a storyboard from that rendered mp4 and review creative intent, pacing, framing, text safety, visual hierarchy, missing glyphs, subtitle-zone readability, the weakest frame, and obvious quality issues before finalizing. Do not rely only on whole-frame pixel ratios or absence of errors; if the storyboard only proves that nothing is broken, revise JSX and render again.",
                "- Once a session already has an active project, treat it as the default target for follow-up edits and renders.",
                "- Use run_after_effects_jsx only for creative edits that are not already covered by the higher-level Shotwright tools.",
                "- If run_after_effects_jsx fails after a project was created, fix the JSX and retry against the same project_id; do not create a replacement workspace unless the project tool reports that the workspace is unrecoverable.",
                "- In After Effects JSX, guard every layer, property, and effect lookup before use; unsupported effects and missing properties often return null or undefined.",
                "- In After Effects JSX, do not call setValue() on animated properties that may already have keyframes; remove existing keys first or use setValueAtTime()/setValueAtKey().",
                "- For normal Shotwright creative work, do not use arbitrary shell commands unless the user explicitly asks for repository/debug work or every relevant Shotwright tool has failed.",
                "- Do not override the container image unless the user explicitly asks for a different image.",
                "- When rendering succeeds, format asset lines separately so Shotwright can render cards: 成片: `file.mp4`, 预览流: `/api/streams/.../index.m3u8`, 分镜图: `storyboard.jpg`, 工程归档: `/api/projects/.../archive`.",
            ]
        )

    def _build_skill_bridge_instructions(self, skill_directories: list[str], skill_names: list[str]) -> str:
        lines = [
            "Repository skill compatibility:",
            "- Copilot-style Shotwright skills are available as workspace SKILL.md files.",
            "- Treat matching skills as operating instructions. If the user names a skill or asks for a skill-defined workflow, apply that SKILL.md before acting.",
        ]
        if skill_directories:
            lines.append(f"- Skill directories: {json.dumps(skill_directories, ensure_ascii=False)}")
        else:
            lines.append("- Skill directories: none found in this workspace.")
        if skill_names:
            lines.append(f"- Available skills: {', '.join(skill_names)}")
            lines.append("- Embedded SKILL.md excerpts:")
            lines.extend(self._load_skill_excerpts(skill_directories))
        return "\n".join(lines)

    def _load_skill_excerpts(
        self,
        skill_directories: list[str],
        *,
        per_skill_limit: int = 4000,
        total_limit: int = 16000,
    ) -> list[str]:
        excerpts: list[str] = []
        total = 0
        for directory in skill_directories:
            try:
                entries = sorted(
                    (entry for entry in os.scandir(directory) if entry.is_dir()),
                    key=lambda entry: entry.name.lower(),
                )
            except FileNotFoundError:
                continue
            for entry in entries:
                skill_path = os.path.join(entry.path, "SKILL.md")
                if not os.path.isfile(skill_path):
                    continue
                try:
                    text = Path(skill_path).read_text(encoding="utf-8")
                except OSError:
                    continue
                remaining = total_limit - total
                if remaining <= 0:
                    return excerpts
                excerpt = text[: min(per_skill_limit, remaining)]
                total += len(excerpt)
                if len(text) > len(excerpt):
                    excerpt = f"{excerpt}\n...[truncated]"
                excerpts.append(f"  - {entry.name} ({skill_path}):\n{excerpt}")
        return excerpts

    def _truncate_recent_context_text(self, content: object) -> str:
        text = re.sub(r"\s+", " ", str(content or "")).strip()
        if len(text) <= _RECENT_CONTEXT_MESSAGE_CHAR_LIMIT:
            return text
        omitted = len(text) - _RECENT_CONTEXT_MESSAGE_CHAR_LIMIT
        return f"{text[:_RECENT_CONTEXT_MESSAGE_CHAR_LIMIT]}...[truncated {omitted} chars]"

    async def _build_recent_message_context(self, app_session_id: str, current_content: str) -> list[str]:
        try:
            message_collection = get_message_collection()
        except AssertionError:
            return []

        try:
            docs = (
                await message_collection.find({"session_id": app_session_id})
                .sort("created_at", -1)
                .limit(_RECENT_CONTEXT_MESSAGE_LIMIT + 2)
                .to_list(length=_RECENT_CONTEXT_MESSAGE_LIMIT + 2)
            )
        except Exception as exc:
            logger.debug("Failed to load recent Shotwright messages for %s: %s", app_session_id, exc)
            return []

        entries: list[tuple[str, str]] = []
        for doc in reversed(docs):
            if not isinstance(doc, dict):
                continue
            role = str(doc.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            text = str(doc.get("content") or "").strip()
            if not text:
                continue
            entries.append((role, text))

        if entries and entries[-1][0] == "user" and entries[-1][1].strip() == current_content.strip():
            entries = entries[:-1]
        entries = entries[-_RECENT_CONTEXT_MESSAGE_LIMIT:]
        if not entries:
            return []

        lines = [
            "Recent conversation context:",
            "- This is a compact reminder because Shotwright starts fresh Codex tool threads to avoid long-context stalls.",
        ]
        for role, text in entries:
            lines.append(f"- {role}: {self._truncate_recent_context_text(text)}")
        return lines

    def _build_tool_bridge_instructions(self, app_session_id: str, tool_manifest: list[dict]) -> str:
        python_path = str(Path(sys.executable).resolve())
        runner_path = str(_TOOL_RUNNER_PATH)
        base_command = (
            f'& "{python_path}" "{runner_path}" --session-id "{app_session_id}"'
        )
        return "\n".join(
            [
                "Shotwright tool compatibility bridge:",
                "- The Codex SDK cannot receive Copilot custom tools in-process, so Shotwright exposes the same backend tools through this JSON CLI.",
                "- For Shotwright operations, use this runner instead of reimplementing tool behavior in shell.",
                "- Read each JSON result and use success, text_result_for_llm, error, and session_log as the tool response.",
                "- Prefer --arguments-json @- with stdin for all non-empty arguments, especially JSX and patch scripts.",
                "- In this Shotwright web runtime, do not call shell_command yourself. Return structured Shotwright tool calls; the backend executes them directly and sends you the JSON result.",
                "- Return one tool call at a time. After each tool result, decide the next tool call or final response.",
                "- For any request to create or render After Effects content, use Shotwright tools before finalizing. Usually call ensure_after_effects_container, create_after_effects_project or create_empty_after_effects_project, then render_after_effects_project, and export_project_archive when useful.",
                "- After a render_after_effects_project success, call generate_storyboard_from_reference_video on that render output and review the storyboard for both correctness and creative quality before final. Do not final immediately after render.",
                "- List tools:",
                f"  {base_command} --list",
                "- Run a tool:",
                "  @'",
                '  {"project_id":"optional-project-id"}',
                "  '@ | "
                f'{base_command} --tool "inspect_workspace" --arguments-json @-',
                "- Available tool manifest:",
                json.dumps(tool_manifest, ensure_ascii=False, indent=2),
            ]
        )

    async def _build_session_context(self, app_session_id: str, content: str) -> str:
        try:
            session_collection = get_session_collection()
            project_collection = get_project_collection()
        except AssertionError:
            return content

        session_doc = await session_collection.find_one({"_id": app_session_id})
        if not session_doc:
            return content

        recent_context = await self._build_recent_message_context(app_session_id, content)
        active_project_id = str(session_doc.get("active_project_id") or "").strip()
        if not active_project_id:
            lines = [*recent_context, "User request:", content] if recent_context else ["User request:", content]
            return "\n".join(lines)

        project_doc = await project_collection.find_one({"_id": active_project_id, "session_id": app_session_id})
        if not project_doc:
            lines = [*recent_context, "User request:", content] if recent_context else ["User request:", content]
            return "\n".join(lines)

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
            ]
        )
        if recent_context:
            preamble_lines.extend(["", *recent_context])
        preamble_lines.extend(["", "User request:", content])
        return "\n".join(preamble_lines)

    def _build_client(
        self,
        runtime_settings: dict[str, str | bool | float],
        *,
        api_key: str,
    ) -> CodexBridgeClient:
        node_path = _first_non_empty(str(runtime_settings.get("codex_node_path") or ""))
        bridge_script = _first_non_empty(str(runtime_settings.get("codex_bridge_script") or ""))
        codex_runtime_home = resolve_codex_runtime_home()
        Path(codex_runtime_home).mkdir(parents=True, exist_ok=True)
        return CodexBridgeClient(
            node_binary=node_path or None,
            bridge_script=bridge_script or None,
            api_key=api_key,
            base_url=str(runtime_settings.get("codex_base_url") or ""),
            disable_responses_websocket=bool(runtime_settings.get("codex_disable_responses_websocket")),
            codex_path_override=str(runtime_settings.get("codex_path_override") or ""),
            extra_env={
                "CODEX_HOME": codex_runtime_home,
                "http_proxy": str(runtime_settings.get("codex_http_proxy") or ""),
                "https_proxy": str(runtime_settings.get("codex_https_proxy") or ""),
                "no_proxy": str(runtime_settings.get("codex_no_proxy") or ""),
            },
        )

    def _build_direct_tool_protocol_instructions(self) -> str:
        return "\n".join(
            [
                "Direct Shotwright tool protocol:",
                "- You must respond only with JSON matching the provided schema.",
                "- Use action=tool_call when a Shotwright backend operation is needed.",
                "- Use action=final only after the requested creative work is complete or no tool is needed.",
                "- If render_after_effects_project succeeded during this turn, action=final is allowed only after generate_storyboard_from_reference_video has also succeeded for that rendered output and you have reviewed whether to revise for correctness and creative quality.",
                "- Do not render a freshly created empty project until run_after_effects_jsx or create_after_effects_project has created the requested composition and inspect_workspace reports real dimensions and a nonzero layer_count. A project whose composition metadata is null is only a workspace shell, not a finished AEP.",
                "- Never call shell_command, never ask to run codex_tool_runner.py, and never claim a render/export exists until a tool result confirms it.",
                "- For tool_call, set tool_name to exactly one available Shotwright tool and arguments_json to a JSON object string for that tool.",
                "- For lyric MV, dense CJK text animation, subtitle-heavy, or text-measurement-sensitive AE work, prefer create_lyrics_mv_project once lyrics_lrc or assets/data/lyric_mapping.json is available; then render, storyboard, and review.",
                "- Do not inline large After Effects JSX in arguments_json. For complex scenes, lyric/MV layouts, many text layers, or scripts over roughly 8 KB, first call run_python_code to write a .jsx file inside the active project workspace, then call run_after_effects_jsx with script_path.",
                "- When recovering from a timeout, choose one small concrete tool action. Do not spend another long model turn writing a full scene inline; create or reuse a script_path and execute it.",
                "- For final, set tool_name to an empty string, arguments_json to {}, and response to the user-facing answer.",
            ]
        )

    def _parse_direct_tool_plan(self, raw_response: str) -> dict[str, object]:
        text = raw_response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
            text = re.sub(r"\s*```$", "", text).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Codex returned invalid Shotwright tool JSON: {raw_response[:500]}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Codex Shotwright tool JSON must be an object.")
        action = str(parsed.get("action") or "").strip()
        if action not in {"tool_call", "final"}:
            raise ValueError(f"Unsupported Codex Shotwright tool action: {action or '<empty>'}")
        arguments_json = str(parsed.get("arguments_json") or "{}").strip() or "{}"
        try:
            arguments = json.loads(arguments_json)
        except json.JSONDecodeError as exc:
            repaired_arguments_json = self._repair_direct_tool_arguments_json(arguments_json)
            if repaired_arguments_json == arguments_json:
                raise ValueError(
                    f"Codex Shotwright tool arguments_json must be a JSON object string: {arguments_json[:500]}"
                ) from exc
            try:
                arguments = json.loads(repaired_arguments_json)
            except json.JSONDecodeError as repaired_exc:
                raise ValueError(
                    f"Codex Shotwright tool arguments_json must be a JSON object string: {arguments_json[:500]}"
                ) from repaired_exc
        if not isinstance(arguments, dict):
            raise ValueError("Codex Shotwright tool arguments_json must decode to a JSON object.")
        parsed["arguments"] = arguments
        parsed["tool_name"] = str(parsed.get("tool_name") or "").strip()
        parsed["response"] = str(parsed.get("response") or "").strip()
        return parsed

    def _repair_direct_tool_arguments_json(self, arguments_json: str) -> str:
        # Codex sometimes mangles JSX regex ranges like \u4e00-\u9fff inside
        # nested JSON strings into raw control characters. Preserve the intended
        # JavaScript escape before falling back to generic JSON control escaping.
        repaired = _MANGLED_CJK_RANGE_TEXT_RE.sub(r"\\u4e00-\\u9fff", arguments_json)
        repaired = _MANGLED_CJK_RANGE_RE.sub(r"\\u4e00-\\u9fff", repaired)

        def escape_control(match: re.Match[str]) -> str:
            ch = match.group(0)
            if ch == "\n":
                return r"\n"
            if ch == "\r":
                return r"\r"
            if ch == "\t":
                return r"\t"
            return f"\\u{ord(ch):04x}"

        return _JSON_CONTROL_CHAR_RE.sub(escape_control, repaired)

    def _looks_like_direct_tool_plan(self, text: str) -> bool:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE).strip()
            stripped = re.sub(r"\s*```$", "", stripped).strip()
        if not stripped.startswith("{"):
            return False
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, dict) and parsed.get("action") in {"tool_call", "final"}

    async def _persist_direct_tool_bridge_events(
        self,
        app_session_id: str,
        runtime: _CodexRuntimeHandle,
        turn_state: _CodexTurnState,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, object],
        payload: dict[str, object] | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        base_data = {
            "provider": "codex",
            "tool_name": tool_name,
            "codex_item_id": tool_call_id,
            "command": None,
            "arguments": arguments,
        }
        if payload is None:
            await self._persist_event(
                app_session_id,
                "tool.execution_start",
                base_data,
                turn_id=turn_state.turn_id,
                sequence=runtime.next_event_sequence(),
            )
            return

        await self._persist_event(
            app_session_id,
            "tool.execution_complete",
            {
                **base_data,
                "success": payload.get("success"),
                "exit_code": None,
                "result_type": payload.get("result_type"),
                "text_result_for_llm": payload.get("text_result_for_llm"),
                "error": payload.get("error"),
                "session_log": payload.get("session_log"),
                "tool_telemetry": payload.get("tool_telemetry"),
                "duration_seconds": duration_seconds,
            },
            turn_id=turn_state.turn_id,
            sequence=runtime.next_event_sequence(),
        )

    def _build_tool_result_followup(self, tool_name: str, payload: dict[str, object]) -> str:
        compact_payload = self._compact_tool_result_for_followup(tool_name, payload)
        lines = [
            "Shotwright backend executed your requested tool call.",
            f"Tool: {tool_name}",
            "Compact result JSON:",
            json.dumps(compact_payload, ensure_ascii=False, indent=2),
            "",
            "Continue using the Direct Shotwright tool protocol. Return the next tool_call if more work is required; otherwise return final.",
        ]
        if tool_name in {"run_python_code", "create_empty_after_effects_project", "inspect_workspace", "create_lyrics_mv_project"}:
            lines.extend(
                [
                    "",
                    "Complex After Effects script handoff rule:",
                    "- For lyric MV, dense CJK text animation, subtitle-heavy, or text-measurement-sensitive work, prefer create_lyrics_mv_project once lyrics_lrc or assets/data/lyric_mapping.json exists.",
                    "- If the next operation needs substantial JSX, many lyric/text layers, or detailed animation logic, do not inline that JSX in arguments_json.",
                    "- Use run_python_code to write a compact generated .jsx file under the active project workspace, then call run_after_effects_jsx with script_path.",
                    "- Keep the next model decision small: either write/reuse the script file or execute an existing script_path.",
                ]
            )
        return "\n".join(lines)

    def _truncate_tool_followup_text(
        self,
        value: object,
        *,
        limit: int = _DIRECT_TOOL_FOLLOWUP_STRING_LIMIT,
    ) -> str | None:
        if value is None:
            return None
        text = str(value)
        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        return f"{text[:limit]}\n...[truncated {omitted} chars; full result is stored in session events]"

    def _select_tool_result_fields(self, value: dict[str, object], fields: tuple[str, ...]) -> dict[str, object]:
        return {field: value[field] for field in fields if field in value and value[field] is not None}

    def _compact_workspace_result(self, parsed: dict[str, object]) -> dict[str, object]:
        raw_projects = parsed.get("projects") if isinstance(parsed.get("projects"), list) else []
        raw_reference_videos = parsed.get("reference_videos") if isinstance(parsed.get("reference_videos"), list) else []
        raw_storyboards = parsed.get("storyboards") if isinstance(parsed.get("storyboards"), list) else []
        raw_render_outputs = parsed.get("render_outputs") if isinstance(parsed.get("render_outputs"), list) else []
        projects = [
            self._select_tool_result_fields(
                project,
                ("_id", "filename", "entry_aep_file", "status", "compositions"),
            )
            for project in raw_projects
            if isinstance(project, dict)
        ]
        reference_videos = [
            self._select_tool_result_fields(
                item,
                ("filename", "shared_relative_path", "duration_seconds", "width", "height", "size_bytes"),
            )
            for item in raw_reference_videos
            if isinstance(item, dict)
        ]
        storyboards = [
            self._select_tool_result_fields(
                item,
                ("filename", "shared_relative_path", "source_video_filename", "interval_seconds", "estimated_frames"),
            )
            for item in raw_storyboards[-6:]
            if isinstance(item, dict)
        ]
        render_outputs = [
            self._select_tool_result_fields(
                item,
                ("filename", "shared_relative_path", "playlist_url", "composition", "size_bytes"),
            )
            for item in raw_render_outputs[:6]
            if isinstance(item, dict)
        ]
        return {
            "session_id": parsed.get("session_id"),
            "status": parsed.get("status"),
            "container": self._select_tool_result_fields(parsed.get("container") or {}, ("status", "image", "docker_id"))
            if isinstance(parsed.get("container"), dict)
            else parsed.get("container"),
            "active_project_id": parsed.get("active_project_id"),
            "projects": projects,
            "reference_videos": reference_videos,
            "storyboards": storyboards,
            "render_outputs": render_outputs,
            "latest_render_path": parsed.get("latest_render_path"),
            "latest_stream_url": parsed.get("latest_stream_url"),
            "font_guidance": parsed.get("recommended_fonts"),
        }

    def _compact_render_result(self, parsed: dict[str, object]) -> dict[str, object]:
        render_output = parsed.get("render_output") if isinstance(parsed.get("render_output"), dict) else {}
        project = parsed.get("project") if isinstance(parsed.get("project"), dict) else {}
        return {
            "success": parsed.get("success"),
            "output_exists": parsed.get("output_exists"),
            "render_completed": parsed.get("render_completed"),
            "timed_out": parsed.get("timed_out"),
            "project_id": parsed.get("project_id"),
            "composition": render_output.get("composition") or parsed.get("composition"),
            "output_path": parsed.get("output_path"),
            "playlist_url": parsed.get("playlist_url"),
            "stream_ready": parsed.get("stream_ready"),
            "render_output": self._select_tool_result_fields(
                render_output,
                ("id", "filename", "file_path", "shared_relative_path", "playlist_url", "thumbnail_path", "size_bytes"),
            ),
            "project": self._select_tool_result_fields(project, ("_id", "filename", "entry_aep_file", "compositions")),
            "stderr_excerpt": self._truncate_tool_followup_text(parsed.get("stderr_excerpt"), limit=900),
        }

    def _compact_jsx_result(self, parsed: dict[str, object]) -> dict[str, object]:
        project = parsed.get("project") if isinstance(parsed.get("project"), dict) else {}
        return {
            "success": parsed.get("success"),
            "timed_out": parsed.get("timed_out"),
            "project_id": parsed.get("project_id"),
            "entry_aep_file": parsed.get("entry_aep_file"),
            "entry_aep_path": parsed.get("entry_aep_path"),
            "compositions": parsed.get("compositions") or project.get("compositions"),
            "jsx_compatibility_rewrites": parsed.get("jsx_compatibility_rewrites"),
            "after_effects_ready": parsed.get("after_effects_ready"),
            "error": parsed.get("error"),
            "output_excerpt": self._truncate_tool_followup_text(parsed.get("output"), limit=1200),
        }

    def _compact_generic_tool_result_value(self, value: object, *, depth: int = 0) -> object:
        if isinstance(value, str):
            return self._truncate_tool_followup_text(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if depth >= 4:
            return self._truncate_tool_followup_text(value, limit=900)
        if isinstance(value, list):
            return [self._compact_generic_tool_result_value(item, depth=depth + 1) for item in value[:12]]
        if isinstance(value, dict):
            noisy_fields = {"stdout", "stderr", "output", "stdout_excerpt", "stderr_excerpt"}
            compact: dict[str, object] = {}
            for key, item in value.items():
                if key in noisy_fields:
                    compact[key] = self._truncate_tool_followup_text(item, limit=900)
                else:
                    compact[key] = self._compact_generic_tool_result_value(item, depth=depth + 1)
            return compact
        return self._truncate_tool_followup_text(value)

    def _compact_tool_text_result(self, tool_name: str, parsed: dict[str, object]) -> dict[str, object]:
        if tool_name == "inspect_workspace":
            return self._compact_workspace_result(parsed)
        if tool_name == "render_after_effects_project":
            return self._compact_render_result(parsed)
        if tool_name == "run_after_effects_jsx":
            return self._compact_jsx_result(parsed)
        return self._compact_generic_tool_result_value(parsed)  # type: ignore[return-value]

    def _compact_tool_result_for_followup(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        compact: dict[str, object] = {
            "success": payload.get("success"),
            "result_type": payload.get("result_type"),
            "error": self._truncate_tool_followup_text(payload.get("error"), limit=1200),
            "session_log": self._truncate_tool_followup_text(payload.get("session_log"), limit=1200),
            "tool_telemetry": self._compact_generic_tool_result_value(payload.get("tool_telemetry")),
        }
        parsed = self._parse_tool_text_payload(payload)
        if parsed:
            compact["result"] = self._compact_tool_text_result(tool_name, parsed)
        else:
            compact["text_result_for_llm"] = self._truncate_tool_followup_text(payload.get("text_result_for_llm"))

        encoded = json.dumps(compact, ensure_ascii=False)
        if len(encoded) <= _DIRECT_TOOL_FOLLOWUP_JSON_LIMIT:
            return compact

        compact["result"] = self._truncate_tool_followup_text(compact.get("result"), limit=_DIRECT_TOOL_FOLLOWUP_JSON_LIMIT)
        return compact

    def _parse_tool_text_payload(self, payload: dict[str, object]) -> dict[str, object]:
        text_result = payload.get("text_result_for_llm")
        if not isinstance(text_result, str) or not text_result.strip():
            return {}
        try:
            parsed = json.loads(text_result)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _extract_render_review_target(self, payload: dict[str, object]) -> dict[str, object] | None:
        if not payload.get("success"):
            return None
        parsed = self._parse_tool_text_payload(payload)
        output_path = _first_non_empty(
            parsed.get("output_path"),
            parsed.get("render_path"),
            (parsed.get("render_output") or {}).get("file_path") if isinstance(parsed.get("render_output"), dict) else "",
            (parsed.get("render_output") or {}).get("shared_relative_path")
            if isinstance(parsed.get("render_output"), dict)
            else "",
        )
        if not output_path:
            return None
        return {
            "output_path": output_path,
            "output_name": Path(str(output_path)).stem,
            "playlist_url": _first_non_empty(parsed.get("playlist_url"), parsed.get("latest_stream_url")),
            "project_id": _first_non_empty(parsed.get("project_id"), parsed.get("active_project_id")),
        }

    def _build_render_review_required_followup(self, pending_render_review: dict[str, object]) -> str:
        render_path = str(pending_render_review.get("output_path") or "").strip()
        output_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(pending_render_review.get("output_name") or "render")).strip("_")
        storyboard_name = f"{output_stem or 'render'}_review_storyboard.jpg"
        arguments = {
            "reference_video_path": render_path,
            "output_name": storyboard_name,
            "interval_seconds": 1.0,
            "columns": 4,
            "width": 360,
            "description": "Review the completed render before finalizing",
        }
        return "\n".join(
            [
                "Shotwright quality gate blocked finalization.",
                "render_after_effects_project succeeded in this turn, but the rendered mp4 has not been storyboard-reviewed yet.",
                "Return a Direct Shotwright tool_call now:",
                json.dumps(
                    {
                        "action": "tool_call",
                        "tool_name": "generate_storyboard_from_reference_video",
                        "arguments_json": json.dumps(arguments, ensure_ascii=False),
                        "response": "Generate a storyboard from the completed render before finalizing.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "After the storyboard tool succeeds, identify the weakest frame and decide whether the result needs a JSX revision and another render. Only final when the render passes both correctness and creative-quality review.",
            ]
        )

    def _parse_independent_render_review_response(self, raw_response: str) -> dict[str, object]:
        text = raw_response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
            text = re.sub(r"\s*```$", "", text).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Independent render reviewer returned invalid JSON: {raw_response[:500]}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Independent render reviewer JSON must be an object.")
        issues = parsed.get("issues")
        if not isinstance(issues, list):
            issues = []
        parsed["issues"] = [str(item).strip() for item in issues if str(item).strip()]
        verdict = str(parsed.get("verdict") or "").strip().lower()
        parsed["verdict"] = "pass" if verdict == "pass" else "fail"
        parsed["blocking"] = bool(parsed.get("blocking")) or parsed["verdict"] == "fail"
        try:
            confidence = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        parsed["confidence"] = max(0.0, min(1.0, confidence))
        parsed["summary"] = str(parsed.get("summary") or "").strip()
        parsed["weakest_frame"] = str(parsed.get("weakest_frame") or "").strip()
        parsed["revision_brief"] = str(parsed.get("revision_brief") or "").strip()
        return parsed

    def _build_independent_render_review_input(
        self,
        *,
        user_goal: str,
        pending_render_review: dict[str, object],
        storyboard_payload: dict[str, object],
        subtitle_storyboard: dict[str, object],
        analysis: dict[str, object],
    ) -> str | list[dict[str, str]]:
        text = "\n".join(
            [
                "You are Shotwright's independent render review agent.",
                "You did not create this video. Do not protect the maker agent's choices.",
                "Your only job is to inspect the attached storyboard images and decide whether the result is acceptable before the maker agent may finalize.",
                "",
                "Return JSON only. Use verdict=fail and blocking=true when the maker must revise and render again.",
                "Fail obvious quality issues even if the render technically succeeded: black-slab subtitles, heavy dark strokes swallowing glyphs, missing glyphs, low-contrast text, clipped or unsafe text, muddy hierarchy, generic template filler, visual choices that contradict the user's requested tone, or a storyboard that only proves nothing crashed.",
                "Do not fail just because a design uses some dark contrast; fail when the dark treatment becomes the visible style, hides text, or feels disconnected from the footage.",
                "",
                "Original user goal:",
                user_goal.strip()[:3000] or "(not available)",
                "",
                "Render under review:",
                json.dumps(pending_render_review, ensure_ascii=False, indent=2),
                "",
                "Backend subtitle-zone metrics. Treat these as risk signals, not as a replacement for looking at the images:",
                json.dumps(analysis, ensure_ascii=False, indent=2),
                "",
                "Attached images:",
                "- Full-frame storyboard from the completed render.",
                "- Focused subtitle-zone storyboard cropped from the same render.",
                "",
                "Review expectations:",
                "- Judge phone-sized readability, caption style fit, and whether the creative direction is visible.",
                "- Name the weakest frame or region.",
                "- If blocking, give a compact revision brief that the maker can execute in After Effects.",
            ]
        )
        return self._build_visual_review_input(
            text,
            storyboard_payload.get("storyboard_image_path"),
            subtitle_storyboard.get("storyboard_image_path"),
        )

    async def _run_independent_render_review(
        self,
        *,
        app_session_id: str,
        runtime: _CodexRuntimeHandle,
        turn_state: _CodexTurnState,
        client: CodexBridgeClient,
        runtime_settings: dict[str, str | bool | float],
        codex_sdk_config: dict[str, object],
        model: str,
        reasoning_effort: str,
        user_goal: str,
        pending_render_review: dict[str, object],
        storyboard_payload: dict[str, object],
        subtitle_storyboard: dict[str, object],
        analysis: dict[str, object],
    ) -> dict[str, object]:
        review_input = self._build_independent_render_review_input(
            user_goal=user_goal,
            pending_render_review=pending_render_review,
            storyboard_payload=storyboard_payload,
            subtitle_storyboard=subtitle_storyboard,
            analysis=analysis,
        )
        await self._persist_event(
            app_session_id,
            "quality.review_start",
            {
                "provider": "codex",
                "reviewer": "independent_render_reviewer",
                "render": pending_render_review,
                "analysis": analysis,
            },
            turn_id=turn_state.turn_id,
            sequence=runtime.next_event_sequence(),
        )
        started_at = monotonic()
        try:
            result = await client.run_turn(
                input=review_input,
                thread_id=None,
                working_directory=str(runtime_settings.get("codex_workspace_root") or ""),
                model=model,
                model_reasoning_effort=reasoning_effort,
                approval_policy="never",
                sandbox_mode="read-only",
                network_access_enabled=False,
                skip_git_repo_check=True,
                web_search_mode="",
                config=codex_sdk_config or None,
                output_schema=_INDEPENDENT_RENDER_REVIEW_SCHEMA,
                on_event=None,
            )
            review = self._parse_independent_render_review_response(result.final_response)
        except Exception as exc:
            review = {
                "verdict": "fail",
                "blocking": True,
                "confidence": 0.0,
                "summary": "Independent render review could not complete, so finalization is blocked.",
                "weakest_frame": "",
                "issues": [str(exc).strip() or exc.__class__.__name__],
                "revision_brief": "Retry the independent review after checking the storyboard images; do not finalize from maker self-review.",
                "error": str(exc),
            }
        duration_seconds = round(monotonic() - started_at, 3)
        await self._persist_event(
            app_session_id,
            "quality.review_complete",
            {
                "provider": "codex",
                "reviewer": "independent_render_reviewer",
                "duration_seconds": duration_seconds,
                "verdict": review.get("verdict"),
                "blocking": review.get("blocking"),
                "confidence": review.get("confidence"),
                "summary": review.get("summary"),
                "weakest_frame": review.get("weakest_frame"),
                "issues": review.get("issues"),
                "revision_brief": review.get("revision_brief"),
                "error": review.get("error"),
            },
            turn_id=turn_state.turn_id,
            sequence=runtime.next_event_sequence(),
        )
        return review

    def _extract_review_user_goal(self, runtime_content: str) -> str:
        marker = "User request:"
        if marker in runtime_content:
            return runtime_content.rsplit(marker, 1)[-1].strip()
        return runtime_content.strip()

    async def _review_render_storyboard_quality(
        self,
        app_session_id: str,
        runtime: _CodexRuntimeHandle,
        turn_state: _CodexTurnState,
        client: CodexBridgeClient,
        runtime_settings: dict[str, str | bool | float],
        codex_sdk_config: dict[str, object],
        pending_render_review: dict[str, object],
        storyboard_payload: dict[str, object],
        *,
        model: str,
        reasoning_effort: str,
        user_goal: str,
    ) -> dict[str, object]:
        render_path = str(pending_render_review.get("output_path") or "").strip()
        if not render_path:
            return {"success": False, "blocking": False, "error": "missing render output path"}

        output_stem = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "_",
            str(pending_render_review.get("output_name") or Path(render_path).stem or "render"),
        ).strip("_")
        try:
            subtitle_storyboard = rm.generate_storyboard(
                app_session_id,
                reference_video_path=render_path,
                output_name=f"{output_stem or 'render'}_subtitle_zone_review.jpg",
                interval_seconds=1.0,
                columns=4,
                width=420,
                crop={"x": "0%", "y": "68%", "width": "100%", "height": "30%"},
            )
            analysis = rm.analyze_storyboard_image_darkness(subtitle_storyboard["storyboard_image_path"])
        except Exception as exc:
            logger.warning("Render storyboard quality review failed for %s: %s", app_session_id, exc)
            return {"success": False, "blocking": False, "error": str(exc)}

        reviewer = await self._run_independent_render_review(
            app_session_id=app_session_id,
            runtime=runtime,
            turn_state=turn_state,
            client=client,
            runtime_settings=runtime_settings,
            codex_sdk_config=codex_sdk_config,
            model=model,
            reasoning_effort=reasoning_effort,
            user_goal=user_goal,
            pending_render_review=pending_render_review,
            storyboard_payload=storyboard_payload,
            subtitle_storyboard=subtitle_storyboard,
            analysis=analysis,
        )
        metric_blocking = bool(analysis.get("caption_quality_risk") or analysis.get("black_caption_risk"))
        reviewer_blocking = bool(reviewer.get("blocking")) or str(reviewer.get("verdict") or "") == "fail"
        return {
            "success": True,
            "blocking": metric_blocking or reviewer_blocking,
            "subtitle_zone_storyboard": subtitle_storyboard,
            "full_storyboard": storyboard_payload,
            "analysis": analysis,
            "reviewer": reviewer,
            "metric_blocking": metric_blocking,
            "reviewer_blocking": reviewer_blocking,
        }

    def _build_render_quality_block_followup(
        self,
        pending_render_review: dict[str, object],
        quality_review: dict[str, object],
    ) -> str | list[dict[str, str]]:
        analysis = quality_review.get("analysis") if isinstance(quality_review.get("analysis"), dict) else {}
        subtitle_storyboard = (
            quality_review.get("subtitle_zone_storyboard")
            if isinstance(quality_review.get("subtitle_zone_storyboard"), dict)
            else {}
        )
        full_storyboard = (
            quality_review.get("full_storyboard") if isinstance(quality_review.get("full_storyboard"), dict) else {}
        )
        reviewer = quality_review.get("reviewer") if isinstance(quality_review.get("reviewer"), dict) else {}
        text = "\n".join(
            [
                "Shotwright quality gate blocked finalization.",
                "The render failed independent review or subtitle-zone metric checks.",
                "This decision is not maker self-review. An independent render review agent and backend visual metrics are preventing finalization.",
                "Inspect the attached storyboard images, then revise the actual design so the captions fit the creative direction and remain readable without turning into a black slab, muddy low-contrast strip, or generic technical fix.",
                "",
                "Render under review:",
                json.dumps(pending_render_review, ensure_ascii=False, indent=2),
                "",
                "Attached images include the full-frame storyboard when available and a focused subtitle-zone storyboard. Use the visuals, not only the metrics, before choosing the fix.",
                "",
                "Subtitle-zone storyboard and metrics:",
                json.dumps(
                    {
                        "storyboard_image_path": subtitle_storyboard.get("storyboard_image_path"),
                        "shared_relative_path": subtitle_storyboard.get("shared_relative_path"),
                        "analysis": analysis,
                        "independent_reviewer": reviewer,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
                "Return a Direct Shotwright tool_call that revises the active project, then render again. Do not final until a new render passes full-frame and subtitle-zone review.",
                "For lyric MV, dense Chinese text, subtitle-heavy, or text-measurement-sensitive work, prefer create_lyrics_mv_project with the active project_id/mapping_path instead of inline JSX. If a custom AE script is unavoidable, write it with run_python_code to a project-local .jsx file and call run_after_effects_jsx with script_path; do not inline large script_content in this repair response.",
            ]
        )
        return self._build_visual_review_input(
            text,
            full_storyboard.get("storyboard_image_path"),
            subtitle_storyboard.get("storyboard_image_path"),
        )

    def _build_render_visual_review_followup(
        self,
        pending_render_review: dict[str, object],
        storyboard_payload: dict[str, object],
        quality_review: dict[str, object],
    ) -> str | list[dict[str, str]]:
        subtitle_storyboard = (
            quality_review.get("subtitle_zone_storyboard")
            if isinstance(quality_review.get("subtitle_zone_storyboard"), dict)
            else {}
        )
        analysis = quality_review.get("analysis") if isinstance(quality_review.get("analysis"), dict) else {}
        reviewer = quality_review.get("reviewer") if isinstance(quality_review.get("reviewer"), dict) else {}
        text = "\n".join(
            [
                "Shotwright backend generated storyboard images for the completed render.",
                "A separate independent render review agent has approved finalization. Do not repeat maker self-review as the deciding gate.",
                "Use the independent review summary below when writing the final response. Only request another revision if the user asked for something clearly missing outside this render review.",
                "",
                "Render under review:",
                json.dumps(pending_render_review, ensure_ascii=False, indent=2),
                "",
                "Storyboard result and subtitle-zone metrics:",
                json.dumps(
                    {
                        "full_storyboard_image_path": storyboard_payload.get("storyboard_image_path"),
                        "full_storyboard_shared_relative_path": storyboard_payload.get("shared_relative_path"),
                        "subtitle_zone_storyboard_image_path": subtitle_storyboard.get("storyboard_image_path"),
                        "subtitle_zone_shared_relative_path": subtitle_storyboard.get("shared_relative_path"),
                        "analysis": analysis,
                        "independent_reviewer": reviewer,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
                "Continue using the Direct Shotwright tool protocol. Return final with the render assets unless the independent reviewer reported a blocking problem.",
            ]
        )
        return self._build_visual_review_input(
            text,
            storyboard_payload.get("storyboard_image_path"),
            subtitle_storyboard.get("storyboard_image_path"),
        )

    def _build_visual_review_input(
        self,
        text: str,
        *image_paths: object,
    ) -> str | list[dict[str, str]]:
        items: list[dict[str, str]] = [{"type": "text", "text": text}]
        seen: set[str] = set()
        for raw_path in image_paths:
            path = str(raw_path or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            try:
                if not Path(path).is_file():
                    continue
            except OSError:
                continue
            items.append({"type": "local_image", "path": path})
        return items if len(items) > 1 else text

    def _direct_tool_bridge_error_text(self, exc: CodexBridgeError) -> str:
        return "\n".join(
            item
            for item in (
                str(exc),
                exc.stderr,
                json.dumps(exc.record, ensure_ascii=False) if exc.record else "",
            )
            if item
        )

    def _is_transient_direct_tool_bridge_error(self, exc: CodexBridgeError) -> bool:
        message = self._direct_tool_bridge_error_text(exc).lower()
        transient_markers = (
            "currently experiencing high demand",
            "reconnecting",
            "rate limit",
            "temporarily unavailable",
            "temporary errors",
            "overloaded",
            "try again",
            "status 429",
            "status 500",
            "status 502",
            "status 503",
            "status 504",
        )
        return any(marker in message for marker in transient_markers)

    def _is_direct_tool_thread_unavailable_error(self, exc: CodexBridgeError) -> bool:
        message = self._direct_tool_bridge_error_text(exc).lower()
        thread_unavailable_markers = (
            "thread not found",
            "failed to record rollout items",
            "stale thread",
            "thread became unavailable",
        )
        return any(marker in message for marker in thread_unavailable_markers)

    def _is_recoverable_direct_tool_bridge_error(self, exc: CodexBridgeError) -> bool:
        message = self._direct_tool_bridge_error_text(exc).lower()
        recoverable_markers = (
            "timeout",
            "temporar",
        )
        return (
            self._is_transient_direct_tool_bridge_error(exc)
            or self._is_direct_tool_thread_unavailable_error(exc)
            or any(marker in message for marker in recoverable_markers)
        )

    def _direct_tool_transient_bridge_retry_delay_seconds(self, attempt: int) -> float:
        if attempt <= 0:
            return 0.0
        return min(
            _DIRECT_TOOL_TRANSIENT_BRIDGE_RETRY_MAX_DELAY_SECONDS,
            _DIRECT_TOOL_TRANSIENT_BRIDGE_RETRY_BASE_DELAY_SECONDS * attempt,
        )

    def _build_direct_tool_thread_recovery_input(self, runtime_content: str, exc: CodexBridgeError) -> str:
        return "\n\n".join(
            [
                runtime_content,
                self._build_direct_tool_protocol_instructions(),
                "Shotwright recovery note:",
                "The previous Codex thread became unavailable during this same Shotwright turn.",
                f"Recoverable bridge error: {str(exc).strip() or exc.__class__.__name__}",
                "Do not rely on prior Codex thread memory. Call inspect_workspace first, then continue the original user request from the current Shotwright session state.",
                "Reuse existing container, active project, generated assets, reference videos, Python analysis files, renders, and storyboards reported by inspect_workspace. Do not recreate a replacement project unless the active project is unrecoverable.",
            ]
        )

    def _usage_input_tokens(self, usage: dict[str, object] | None) -> int:
        if not isinstance(usage, dict):
            return 0
        candidates: list[int] = []

        def collect(value: object, key_hint: str = "") -> None:
            if isinstance(value, bool):
                return
            if isinstance(value, (int, float)):
                normalized_key = key_hint.lower()
                if "token" in normalized_key and ("input" in normalized_key or "prompt" in normalized_key):
                    candidates.append(int(value))
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    collect(item, str(key))
                return
            if isinstance(value, list):
                for item in value:
                    collect(item, key_hint)

        collect(usage)
        for key in ("input_tokens", "prompt_tokens", "total_input_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                candidates.append(int(value))
        return max(candidates, default=0)

    def _direct_tool_thread_reset_reason(self, step_index: int, usage: dict[str, object] | None) -> tuple[bool, str, int]:
        input_tokens = self._usage_input_tokens(usage)
        if input_tokens >= _DIRECT_TOOL_THREAD_RESET_INPUT_TOKEN_THRESHOLD:
            return True, f"input token count reached {input_tokens}", input_tokens
        if step_index > 0 and step_index % _DIRECT_TOOL_THREAD_RESET_STEP_INTERVAL == 0:
            return True, f"completed {step_index} direct tool steps", input_tokens
        return False, "", input_tokens

    def _effective_direct_tool_model_timeout(self, turn_timeout_seconds: float | None) -> float | None:
        if not turn_timeout_seconds or turn_timeout_seconds <= 0:
            return _DIRECT_TOOL_MODEL_CALL_TIMEOUT_CAP_SECONDS
        return min(float(turn_timeout_seconds), _DIRECT_TOOL_MODEL_CALL_TIMEOUT_CAP_SECONDS)

    def _effective_direct_tool_reasoning_effort(self, reasoning_effort: str) -> str:
        normalized = str(reasoning_effort or "").strip().lower()
        if normalized in {"xhigh", "high"}:
            return "medium"
        return reasoning_effort

    def _direct_tool_reasoning_after_timeout(self, reasoning_effort: str, timeout_attempt: int) -> str:
        effective = self._effective_direct_tool_reasoning_effort(reasoning_effort)
        normalized = str(effective or "").strip().lower()
        if timeout_attempt <= 0:
            return effective
        if timeout_attempt >= 1 and normalized == "medium":
            return "medium"
        return effective

    def _build_direct_tool_context_reset_input(
        self,
        runtime_content: str,
        *,
        reason: str,
        last_tool_name: str | None = None,
        last_payload: dict[str, object] | None = None,
        pending_render_review: dict[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        lines = [
            runtime_content,
            self._build_direct_tool_protocol_instructions(),
            "Shotwright context reset note:",
            "The backend intentionally started a fresh Codex thread to avoid long-context stalls.",
            f"Reset reason: {reason}",
            "Do not rely on prior Codex thread memory. Continue the original user request from Shotwright's persisted workspace state.",
            "Reuse existing container, active project, generated assets, reference videos, Python analysis files, renders, and storyboards reported by inspect_workspace. Do not create placeholder validation renders or replacement projects unless the active project is unrecoverable.",
            "If inspect_workspace shows an active generated project whose compositions have null width/height/duration/layer_count, treat it as an empty managed workspace shell. Run create_lyrics_mv_project for lyric/text-heavy work, otherwise run run_after_effects_jsx or create_after_effects_project before rendering.",
        ]
        if timeout_seconds and timeout_seconds > 0:
            lines.append(
                f"The previous model call exceeded {timeout_seconds:g} seconds. Recover by inspecting workspace state and choosing the next concrete Shotwright tool action."
            )
            lines.extend(
                [
                    "Timeout recovery rule:",
                    "- If this is lyric MV, dense CJK text animation, subtitle-heavy, or text-measurement-sensitive work, call create_lyrics_mv_project instead of writing a full custom JSX scene.",
                    "- Do not attempt to generate a large After Effects script inline in this model response.",
                    "- If the task still needs AE JSX, call run_python_code to write a smaller .jsx file in the active project workspace, then call run_after_effects_jsx with script_path.",
                    "- If a suitable .jsx file already exists in the last tool result or project workspace, call run_after_effects_jsx with that script_path now.",
                    "- Prefer a 40-55 second preview that proves the requested mechanics over a full-length render when the prior attempt stalled.",
                ]
            )
        if last_tool_name and last_payload is not None:
            lines.extend(
                [
                    "Last completed backend tool call before the context reset:",
                    json.dumps(
                        {
                            "tool_name": last_tool_name,
                            "compact_result": self._compact_tool_result_for_followup(last_tool_name, last_payload),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                ]
            )
        if pending_render_review:
            lines.extend(
                [
                    "Pending render review requirement:",
                    json.dumps(pending_render_review, ensure_ascii=False, indent=2),
                    "If the pending render exists, generate a storyboard for that exact output and pass the quality gate before any final response.",
                ]
            )
        if last_tool_name and last_payload is not None:
            lines.append(
                "Do not repeat inspect_workspace just because a reset happened when the compact last tool result above already gives enough state. Choose the next concrete Shotwright tool action."
            )
        else:
            lines.append(
                "Call inspect_workspace first after this reset unless the pending render review requirement above makes the next required tool unambiguous."
            )
        return "\n\n".join(lines)

    def _storyboard_matches_pending_render(self, arguments: dict[str, object], pending_render_review: dict[str, object]) -> bool:
        requested_path = str(arguments.get("reference_video_path") or "").strip()
        render_path = str(pending_render_review.get("output_path") or "").strip()
        if not requested_path or not render_path:
            return False
        if requested_path == render_path:
            return True
        return Path(requested_path).name.lower() == Path(render_path).name.lower()

    async def _run_direct_tool_loop(
        self,
        *,
        app_session_id: str,
        runtime: _CodexRuntimeHandle,
        turn_state: _CodexTurnState,
        client: CodexBridgeClient,
        runtime_content: str,
        persisted_attachments: list[dict],
        thread_id: str | None,
        runtime_settings: dict[str, str | bool | float],
        session_doc: dict,
        codex_sdk_config: dict[str, object],
        on_event,
        turn_timeout_seconds: float | None = None,
    ):
        model = str(session_doc.get("copilot_model") or runtime_settings.get("codex_model") or "")
        reasoning_effort = str(
            session_doc.get("copilot_reasoning_effort")
            or runtime_settings.get("codex_reasoning_effort")
            or ""
        )
        current_thread_id = thread_id or None
        current_input: str | list[dict[str, str]] = build_codex_input(
            "\n\n".join([runtime_content, self._build_direct_tool_protocol_instructions()]),
            persisted_attachments,
        )
        pending_render_review: dict[str, object] | None = None
        thread_recovery_attempts = 0
        transient_bridge_attempts = 0
        timeout_recovery_attempts = 0
        model_call_timeout_seconds = self._effective_direct_tool_model_timeout(turn_timeout_seconds)
        current_reasoning_effort = self._effective_direct_tool_reasoning_effort(reasoning_effort)
        last_completed_tool_name: str | None = None
        last_completed_payload: dict[str, object] | None = None

        for step_index in range(1, _DIRECT_TOOL_MAX_STEPS + 1):
            try:
                run_turn = client.run_turn(
                    input=current_input,
                    thread_id=current_thread_id,
                    working_directory=str(runtime_settings.get("codex_workspace_root") or ""),
                    model=model,
                    model_reasoning_effort=current_reasoning_effort,
                    approval_policy=str(runtime_settings.get("codex_approval_policy") or ""),
                    sandbox_mode=str(runtime_settings.get("codex_sandbox_mode") or ""),
                    network_access_enabled=bool(runtime_settings.get("codex_network_access_enabled")),
                    skip_git_repo_check=bool(runtime_settings.get("codex_skip_git_repo_check")),
                    web_search_mode=str(runtime_settings.get("codex_web_search_mode") or ""),
                    config=codex_sdk_config or None,
                    output_schema=_DIRECT_TOOL_PLAN_SCHEMA,
                    on_event=on_event,
                )
                result = (
                    await asyncio.wait_for(run_turn, timeout=model_call_timeout_seconds)
                    if model_call_timeout_seconds and model_call_timeout_seconds > 0
                    else await run_turn
                )
            except TimeoutError as exc:
                if timeout_recovery_attempts >= _DIRECT_TOOL_TIMEOUT_RECOVERY_RETRIES:
                    timeout_text = (
                        f"Shotwright timed out waiting for a Codex model tool decision after "
                        f"{model_call_timeout_seconds:g} seconds."
                    )
                    raise TimeoutError(timeout_text) from exc
                timeout_recovery_attempts += 1
                await self._persist_event(
                    app_session_id,
                    "codex.thread.recovered",
                    {
                        "provider": "codex",
                        "attempt": timeout_recovery_attempts,
                        "previous_thread_id": current_thread_id,
                        "reason": "turn_timeout",
                        "timeout_seconds": turn_timeout_seconds,
                        "model_call_timeout_seconds": model_call_timeout_seconds,
                        "previous_reasoning_effort": current_reasoning_effort,
                        "next_reasoning_effort": self._direct_tool_reasoning_after_timeout(
                            reasoning_effort,
                            timeout_recovery_attempts,
                        ),
                        "message": str(exc).strip() or "Codex model call timed out",
                    },
                    turn_id=turn_state.turn_id,
                    sequence=runtime.next_event_sequence(),
                )
                current_thread_id = None
                current_reasoning_effort = self._direct_tool_reasoning_after_timeout(
                    reasoning_effort,
                    timeout_recovery_attempts,
                )
                current_input = self._build_direct_tool_context_reset_input(
                    runtime_content,
                    reason="model call timeout",
                    last_tool_name=last_completed_tool_name,
                    last_payload=last_completed_payload,
                    pending_render_review=pending_render_review,
                    timeout_seconds=model_call_timeout_seconds,
                )
                continue
            except CodexBridgeError as exc:
                is_transient_bridge_error = self._is_transient_direct_tool_bridge_error(exc)
                is_thread_unavailable_error = self._is_direct_tool_thread_unavailable_error(exc)
                if not self._is_recoverable_direct_tool_bridge_error(exc):
                    raise

                if is_transient_bridge_error:
                    if transient_bridge_attempts >= _DIRECT_TOOL_TRANSIENT_BRIDGE_RETRIES:
                        raise
                    transient_bridge_attempts += 1
                    recovery_attempt = transient_bridge_attempts
                    recovery_reason = "transient_bridge_error"
                    retry_delay_seconds = self._direct_tool_transient_bridge_retry_delay_seconds(recovery_attempt)
                else:
                    if thread_recovery_attempts >= _DIRECT_TOOL_THREAD_RECOVERY_RETRIES:
                        raise
                    thread_recovery_attempts += 1
                    recovery_attempt = thread_recovery_attempts
                    recovery_reason = "thread_unavailable" if is_thread_unavailable_error else "bridge_error"
                    retry_delay_seconds = 0.0

                await self._persist_event(
                    app_session_id,
                    "codex.thread.recovered",
                    {
                        "provider": "codex",
                        "attempt": recovery_attempt,
                        "previous_thread_id": current_thread_id,
                        "reason": recovery_reason,
                        "retry_delay_seconds": retry_delay_seconds,
                        "message": str(exc).strip() or exc.__class__.__name__,
                    },
                    turn_id=turn_state.turn_id,
                    sequence=runtime.next_event_sequence(),
                )
                if retry_delay_seconds > 0:
                    await asyncio.sleep(retry_delay_seconds)
                current_thread_id = None
                current_input = self._build_direct_tool_context_reset_input(
                    runtime_content,
                    reason=recovery_reason.replace("_", " "),
                    last_tool_name=last_completed_tool_name,
                    last_payload=last_completed_payload,
                    pending_render_review=pending_render_review,
                )
                continue
            current_thread_id = result.thread_id or current_thread_id
            thread_recovery_attempts = 0
            transient_bridge_attempts = 0
            timeout_recovery_attempts = 0
            plan = self._parse_direct_tool_plan(result.final_response)
            action = str(plan["action"])
            if action == "final":
                if pending_render_review is not None:
                    quality_review = pending_render_review.get("quality_review")
                    if isinstance(quality_review, dict) and quality_review.get("blocking"):
                        current_input = self._build_render_quality_block_followup(pending_render_review, quality_review)
                    else:
                        current_input = self._build_render_review_required_followup(pending_render_review)
                    continue
                final_response = str(plan.get("response") or "").strip()
                if not final_response:
                    final_response = "Shotwright completed the requested work."
                return result.__class__(
                    thread_id=current_thread_id,
                    final_response=final_response,
                    usage=result.usage,
                    events=result.events,
                )

            tool_name = str(plan.get("tool_name") or "").strip()
            if not tool_name:
                raise ValueError("Codex requested a Shotwright tool_call without a tool_name.")
            arguments = plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}
            tool_call_id = f"direct-tool-{step_index}"
            await self._persist_direct_tool_bridge_events(
                app_session_id,
                runtime,
                turn_state,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            tool_started_at = monotonic()
            try:
                payload = await run_codex_tool(
                    app_session_id,
                    tool_name,
                    arguments,
                    tool_call_id=tool_call_id,
                )
            except Exception as exc:
                await self._persist_direct_tool_bridge_events(
                    app_session_id,
                    runtime,
                    turn_state,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    payload={
                        "success": False,
                        "result_type": "failure",
                        "error": str(exc),
                    },
                    duration_seconds=round(monotonic() - tool_started_at, 3),
                )
                raise
            await self._persist_direct_tool_bridge_events(
                app_session_id,
                runtime,
                turn_state,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                payload=payload,
                duration_seconds=round(monotonic() - tool_started_at, 3),
            )
            last_completed_tool_name = tool_name
            last_completed_payload = payload
            if tool_name == "render_after_effects_project":
                pending_render_review = self._extract_render_review_target(payload)
            elif (
                tool_name == "generate_storyboard_from_reference_video"
                and payload.get("success")
                and pending_render_review is not None
                and self._storyboard_matches_pending_render(arguments, pending_render_review)
            ):
                storyboard_payload = self._parse_tool_text_payload(payload)
                quality_review = await self._review_render_storyboard_quality(
                    app_session_id,
                    runtime,
                    turn_state,
                    client,
                    runtime_settings,
                    codex_sdk_config,
                    pending_render_review,
                    storyboard_payload,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    user_goal=self._extract_review_user_goal(runtime_content),
                )
                pending_render_review["quality_review"] = quality_review
                if quality_review.get("blocking"):
                    current_input = self._build_render_quality_block_followup(pending_render_review, quality_review)
                    continue
                current_thread_id = None
                current_input = self._build_render_visual_review_followup(
                    pending_render_review,
                    storyboard_payload,
                    quality_review,
                )
                pending_render_review = None
                continue
            should_reset_thread, reset_reason, usage_input_tokens = self._direct_tool_thread_reset_reason(
                step_index,
                result.usage,
            )
            if should_reset_thread:
                await self._persist_event(
                    app_session_id,
                    "codex.thread.compacted",
                    {
                        "provider": "codex",
                        "previous_thread_id": current_thread_id,
                        "reason": reset_reason,
                        "step_index": step_index,
                        "usage_input_tokens": usage_input_tokens,
                        "last_tool_name": tool_name,
                    },
                    turn_id=turn_state.turn_id,
                    sequence=runtime.next_event_sequence(),
                )
                current_thread_id = None
                current_input = self._build_direct_tool_context_reset_input(
                    runtime_content,
                    reason=reset_reason,
                    last_tool_name=tool_name,
                    last_payload=payload,
                    pending_render_review=pending_render_review,
                )
            else:
                current_input = self._build_tool_result_followup(tool_name, payload)

        raise ValueError(
            f"Codex did not produce a final Shotwright response after {_DIRECT_TOOL_MAX_STEPS} tool steps."
        )

    def _extract_text_value(self, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            return "\n".join(self._extract_text_value(item) for item in value if item is not None).strip()
        if isinstance(value, dict):
            for key in ("text", "content", "output", "stdout", "stderr", "command", "cmd"):
                text = self._extract_text_value(value.get(key))
                if text:
                    return text
            return json.dumps(value, ensure_ascii=False)
        return ""

    def _extract_command_text(self, event: dict, item: dict) -> str:
        for value in (
            item.get("command"),
            item.get("cmd"),
            item.get("input"),
            item.get("arguments"),
            event.get("command"),
            event.get("cmd"),
        ):
            text = self._extract_text_value(value).strip()
            if text:
                return text
        return ""

    def _extract_command_output(self, event: dict, item: dict) -> str:
        for value in (
            item.get("output"),
            item.get("stdout"),
            item.get("stderr"),
            item.get("aggregated_output"),
            item.get("text"),
            event.get("output"),
        ):
            text = self._extract_text_value(value).strip()
            if text:
                return text
        return ""

    def _parse_shell_arg(self, command: str, name: str) -> str:
        pattern = re.compile(_SHELL_ARG_RE_TEMPLATE.format(name=re.escape(name)), flags=re.IGNORECASE)
        match = pattern.search(command)
        if not match:
            return ""
        return next((group for group in match.groups() if group), "").strip()

    def _parse_runner_payload(self, output: str) -> dict | None:
        for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
            if not (line.startswith("{") and line.endswith("}")):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("type") == "shotwright_tool_result":
                return payload
        return None

    def _extract_exit_code(self, event: dict, item: dict) -> int | None:
        for value in (item.get("exit_code"), item.get("exitCode"), item.get("return_code"), event.get("exit_code")):
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                return int(value)
        return None

    def _extract_codex_tool_invocation(self, event: dict, item: dict) -> dict | None:
        command = self._extract_command_text(event, item)
        output = self._extract_command_output(event, item)
        payload = self._parse_runner_payload(output)
        if not payload and (not command or not _CODEX_TOOL_COMMAND_RE.search(command)):
            return None

        tool_name = _first_non_empty(
            str((payload or {}).get("tool_name") or ""),
            self._parse_shell_arg(command, "tool"),
        )
        if not tool_name:
            return None

        event_type = str(event.get("type") or "")
        status = str(item.get("status") or event.get("status") or "").lower()
        exit_code = self._extract_exit_code(event, item)
        is_complete = (
            "completed" in event_type
            or "complete" in status
            or "failed" in event_type
            or "failed" in status
            or exit_code is not None
            or payload is not None
        )
        is_start = "started" in event_type or "start" in status or not is_complete
        item_key = _first_non_empty(str(item.get("id") or ""), str(event.get("id") or ""), command, tool_name)

        success: bool | None = None
        if isinstance((payload or {}).get("success"), bool):
            success = bool(payload["success"])
        elif exit_code is not None:
            success = exit_code == 0
        elif "failed" in event_type or "failed" in status:
            success = False

        return {
            "item_key": item_key,
            "item_id": _first_non_empty(str(item.get("id") or ""), str(event.get("id") or "")),
            "tool_name": tool_name,
            "command": command,
            "output": output,
            "payload": payload,
            "exit_code": exit_code,
            "success": success,
            "is_start": is_start,
            "is_complete": is_complete,
        }

    def _truncate_event_text(self, value: str, limit: int = 4000) -> str:
        if len(value) <= limit:
            return value
        return f"{value[:limit]}..."

    async def _persist_codex_tool_bridge_events(
        self,
        app_session_id: str,
        runtime: _CodexRuntimeHandle,
        turn_state: _CodexTurnState,
        event: dict,
        item: dict,
    ) -> None:
        invocation = self._extract_codex_tool_invocation(event, item)
        if not invocation:
            return

        item_key = str(invocation["item_key"])
        tool_name = str(invocation["tool_name"])
        base_data = {
            "provider": "codex",
            "tool_name": tool_name,
            "codex_item_id": invocation.get("item_id") or None,
            "command": invocation.get("command") or None,
        }
        if invocation["is_start"] and item_key not in turn_state.tool_items_started:
            turn_state.tool_items_started.add(item_key)
            turn_state.tool_item_started_at[item_key] = monotonic()
            await self._persist_event(
                app_session_id,
                "tool.execution_start",
                base_data,
                turn_id=turn_state.turn_id,
                sequence=runtime.next_event_sequence(),
            )

        if not invocation["is_complete"] or item_key in turn_state.tool_items_completed:
            return

        if item_key not in turn_state.tool_items_started:
            turn_state.tool_items_started.add(item_key)
            turn_state.tool_item_started_at[item_key] = monotonic()
            await self._persist_event(
                app_session_id,
                "tool.execution_start",
                base_data,
                turn_id=turn_state.turn_id,
                sequence=runtime.next_event_sequence(),
            )

        turn_state.tool_items_completed.add(item_key)
        started_at = turn_state.tool_item_started_at.pop(item_key, None)
        payload = invocation.get("payload") if isinstance(invocation.get("payload"), dict) else {}
        output = str(invocation.get("output") or "")
        complete_data = {
            **base_data,
            "success": invocation.get("success"),
            "exit_code": invocation.get("exit_code"),
            "result_type": payload.get("result_type"),
            "text_result_for_llm": payload.get("text_result_for_llm") or self._truncate_event_text(output),
            "error": payload.get("error"),
            "session_log": payload.get("session_log"),
            "tool_telemetry": payload.get("tool_telemetry"),
        }
        if started_at is not None:
            complete_data["duration_seconds"] = round(monotonic() - started_at, 3)
        await self._persist_event(
            app_session_id,
            "tool.execution_complete",
            complete_data,
            turn_id=turn_state.turn_id,
            sequence=runtime.next_event_sequence(),
        )

    async def _handle_codex_event(
        self,
        app_session_id: str,
        runtime: _CodexRuntimeHandle,
        turn_state: _CodexTurnState,
        event: dict,
    ) -> None:
        event_type = str(event.get("type") or "unknown")
        if event_type == "thread.started":
            thread_id = str(event.get("thread_id") or "").strip()
            if thread_id:
                turn_state.latest_thread_id = thread_id
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        assistant_text = item.get("text") if item.get("type") == "agent_message" else None
        if isinstance(assistant_text, str) and self._looks_like_direct_tool_plan(assistant_text):
            try:
                plan = self._parse_direct_tool_plan(assistant_text)
            except ValueError:
                plan = {}
            if str(plan.get("action") or "") == "final":
                turn_state.direct_final_response = str(plan.get("response") or "").strip()
        if (
            isinstance(assistant_text, str)
            and assistant_text != turn_state.content
            and not self._looks_like_direct_tool_plan(assistant_text)
        ):
            turn_state.content = assistant_text
            turn_state.version += 1
            await self._sync_streaming_message(
                turn_state,
                content=turn_state.content,
                version=turn_state.version,
                streaming=True,
                state="streaming",
            )

        await self._persist_event(
            app_session_id,
            f"codex.{event_type}",
            event,
            turn_id=turn_state.turn_id,
            sequence=runtime.next_event_sequence(),
        )
        await self._persist_codex_tool_bridge_events(app_session_id, runtime, turn_state, event, item)

    async def send_message(self, app_session_id: str, content: str, attachments: list[dict] | None = None) -> dict:
        turn_id = str(uuid4())
        runtime = await self.ensure_runtime(app_session_id)
        turn_timeout_seconds = await self.resolve_turn_timeout_seconds()
        _, persisted_attachments = _prepare_turn_attachments(
            attachments,
            app_session_id=app_session_id,
            storage_root=settings.upload_dir,
        )

        await self._set_session_status(app_session_id, "running", last_error=None, agent_provider="codex")
        async with runtime.lock:
            runtime.current_task = asyncio.current_task()
            runtime.cancel_requested = False
            user_metadata = {"turn_id": turn_id, "kind": "user_prompt", "provider": "codex"}
            if persisted_attachments:
                user_metadata["attachments"] = persisted_attachments
            await self._persist_message(app_session_id, "user", content, user_metadata)
            await maybe_auto_title_session(app_session_id, content, persisted_attachments)

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
                    "provider": "codex",
                    "streaming": True,
                    "state": "pending",
                    "version": 0,
                },
            )
            turn_state = _CodexTurnState(assistant_doc["_id"], turn_id=turn_id)

            try:
                admin_doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
                runtime_settings = await self._resolve_runtime_settings()
                api_key = resolve_openai_api_key(admin_doc)
                if not api_key:
                    raise ValueError("OpenAI API key is not configured for Codex bridge")

                client = self._build_client(runtime_settings, api_key=api_key)
                runtime_content = await self._build_runtime_turn_content(app_session_id, content)
                session_doc = await get_session_collection().find_one({"_id": app_session_id}) or {}
                stored_thread_id = _first_non_empty(
                    session_doc.get("agent_thread_id"),
                    session_doc.get("codex_thread_id"),
                )
                thread_id = None
                local_profile = load_local_codex_profile()
                codex_sdk_config = build_codex_sdk_config(local_profile)

                await self._persist_event(
                    app_session_id,
                    "session.turn.started",
                    {
                        "provider": "codex",
                        "thread_id": None,
                        "previous_thread_id": stored_thread_id or None,
                        "fresh_thread": True,
                        "attachment_count": len(persisted_attachments),
                        "attachment_mime_types": [
                            attachment.get("mime_type") for attachment in persisted_attachments
                        ],
                        "timeout_seconds": turn_timeout_seconds,
                        "model_call_timeout_cap_seconds": _DIRECT_TOOL_MODEL_CALL_TIMEOUT_CAP_SECONDS,
                        "effective_model_call_timeout_seconds": self._effective_direct_tool_model_timeout(
                            turn_timeout_seconds
                        ),
                        "direct_tool_reasoning_effort": self._effective_direct_tool_reasoning_effort(
                            str(session_doc.get("copilot_reasoning_effort") or runtime_settings.get("codex_reasoning_effort") or "")
                        ),
                        "context_strategy": "fresh_direct_tool_thread_with_compact_session_context",
                    },
                    turn_id=turn_id,
                    sequence=runtime.next_event_sequence(),
                )

                for skill_invocation in self._resolve_matching_skill_invocations(runtime_settings, content):
                    await self._persist_event(
                        app_session_id,
                        "skill.invoked",
                        {
                            "provider": "codex",
                            "source": "embedded_repo_skill",
                            **skill_invocation,
                        },
                        turn_id=turn_id,
                        sequence=runtime.next_event_sequence(),
                    )

                result = await self._run_direct_tool_loop(
                    app_session_id=app_session_id,
                    runtime=runtime,
                    turn_state=turn_state,
                    client=client,
                    runtime_content=runtime_content,
                    persisted_attachments=persisted_attachments,
                    thread_id=thread_id or None,
                    runtime_settings=runtime_settings,
                    session_doc=session_doc,
                    codex_sdk_config=codex_sdk_config,
                    on_event=lambda event: self._handle_codex_event(
                        app_session_id,
                        runtime,
                        turn_state,
                        event,
                    ),
                    turn_timeout_seconds=turn_timeout_seconds,
                )

                assistant_text = result.final_response.strip() or turn_state.content.strip()
                if not assistant_text:
                    assistant_text = (
                        "Shotwright completed the requested work. Inspect the updated session state for the active project, "
                        "renders, or other artifacts."
                    )
                turn_state.content = assistant_text
                turn_state.version += 1
                updated_doc = await self._sync_streaming_message(
                    turn_state,
                    content=assistant_text,
                    version=turn_state.version,
                    streaming=False,
                    state="completed",
                )
                if updated_doc:
                    assistant_doc = updated_doc

                session_extra = {"last_error": None, "agent_provider": "codex"}
                if result.thread_id:
                    session_extra["agent_thread_id"] = result.thread_id
                    session_extra["codex_thread_id"] = result.thread_id
                await self._set_session_status(app_session_id, "idle", **session_extra)
                return {
                    "assistant_message": assistant_doc,
                    "session_status": "idle",
                }
            except asyncio.CancelledError:
                if runtime.cancel_requested:
                    _clear_current_task_cancellation()
                    cancellation_message = "Generation stopped by user."
                    turn_state.version += 1
                    updated_doc = await self._sync_streaming_message(
                        turn_state,
                        content=turn_state.content.strip() or cancellation_message,
                        version=turn_state.version,
                        streaming=False,
                        state="cancelled",
                    )
                    if updated_doc:
                        assistant_doc = updated_doc
                    await self._persist_event(
                        app_session_id,
                        "session.cancelled",
                        {
                            "provider": "codex",
                            "message": cancellation_message,
                            "partial_output": bool(turn_state.content.strip()),
                        },
                        turn_id=turn_id,
                        sequence=runtime.next_event_sequence(),
                    )
                    await self._set_session_status(app_session_id, "idle", last_error=None)
                    return {
                        "assistant_message": assistant_doc,
                        "session_status": "idle",
                    }
                raise
            except TimeoutError as exc:
                timeout_message = str(exc).strip() or (
                    f"Shotwright timed out waiting for this Codex turn after {turn_timeout_seconds:g} seconds."
                )
                if turn_state.direct_final_response:
                    assistant_text = turn_state.direct_final_response
                    logger.warning(
                        "Codex turn for %s hit the timeout after a final Direct Tool response was received; marking completed.",
                        app_session_id,
                    )
                    turn_state.content = assistant_text
                    turn_state.version += 1
                    updated_doc = await self._sync_streaming_message(
                        turn_state,
                        content=assistant_text,
                        version=turn_state.version,
                        streaming=False,
                        state="completed",
                    )
                    if updated_doc:
                        assistant_doc = updated_doc
                    await self._persist_event(
                        app_session_id,
                        "session.timeout_recovered",
                        {
                            "provider": "codex",
                            "message": timeout_message,
                            "timeout_seconds": turn_timeout_seconds,
                            "reason": "direct_final_response_received_before_timeout",
                        },
                        turn_id=turn_id,
                        sequence=runtime.next_event_sequence(),
                    )
                    session_extra = {"last_error": None, "agent_provider": "codex"}
                    if turn_state.latest_thread_id:
                        session_extra["agent_thread_id"] = turn_state.latest_thread_id
                        session_extra["codex_thread_id"] = turn_state.latest_thread_id
                    await self._set_session_status(app_session_id, "idle", **session_extra)
                    return {
                        "assistant_message": assistant_doc,
                        "session_status": "idle",
                    }
                logger.warning("Codex turn timed out for %s after %ss", app_session_id, turn_timeout_seconds)
                turn_state.version += 1
                updated_doc = await self._sync_streaming_message(
                    turn_state,
                    content=turn_state.content.strip() or timeout_message,
                    version=turn_state.version,
                    streaming=False,
                    state="error",
                )
                if updated_doc:
                    assistant_doc = updated_doc
                await self._persist_event(
                    app_session_id,
                    "session.timeout",
                    {
                        "provider": "codex",
                        "message": timeout_message,
                        "timeout_seconds": turn_timeout_seconds,
                        "partial_output": bool(turn_state.content.strip()),
                    },
                    turn_id=turn_id,
                    sequence=runtime.next_event_sequence(),
                )
                await self._set_session_status(app_session_id, "error", last_error=timeout_message)
                return {
                    "assistant_message": assistant_doc,
                    "session_status": "error",
                }
            except (CodexBridgeError, ValueError) as exc:
                error_message = str(exc).strip() or exc.__class__.__name__
                logger.warning("Codex turn failed for %s: %s", app_session_id, error_message)
                turn_state.version += 1
                updated_doc = await self._sync_streaming_message(
                    turn_state,
                    content=turn_state.content.strip() or error_message,
                    version=turn_state.version,
                    streaming=False,
                    state="error",
                )
                if updated_doc:
                    assistant_doc = updated_doc
                await self._persist_event(
                    app_session_id,
                    "session.error",
                    {"provider": "codex", "message": error_message},
                    turn_id=turn_id,
                    sequence=runtime.next_event_sequence(),
                )
                await self._set_session_status(app_session_id, "error", last_error=error_message)
                return {
                    "assistant_message": assistant_doc,
                    "session_status": "error",
                }
            finally:
                if runtime.current_task is asyncio.current_task():
                    runtime.current_task = None
                    runtime.cancel_requested = False

    async def cancel_turn(self, app_session_id: str) -> bool:
        runtime = self._runtimes.get(app_session_id)
        if not runtime or not runtime.current_task or runtime.current_task.done():
            await self.reconcile_session_status(app_session_id)
            return False
        if runtime.current_task is asyncio.current_task():
            return False
        runtime.cancel_requested = True
        runtime.current_task.cancel()
        return True

    async def disconnect_session(self, app_session_id: str) -> None:
        runtime = self._runtimes.pop(app_session_id, None)
        if runtime and runtime.current_task and not runtime.current_task.done():
            runtime.cancel_requested = True
            runtime.current_task.cancel()

    async def shutdown(self) -> None:
        runtimes = list(self._runtimes.values())
        self._runtimes.clear()
        for runtime in runtimes:
            if runtime.current_task and not runtime.current_task.done():
                runtime.cancel_requested = True
                runtime.current_task.cancel()


runtime_manager = ShotwrightCodexRuntimeManager()
