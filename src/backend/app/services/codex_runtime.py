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

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TOOL_RUNNER_PATH = _REPO_ROOT / "src" / "backend" / "app" / "services" / "codex_tool_runner.py"
_REPO_SKILL_DIRECTORIES = (".github", "skills"), (".agents", "skills"), (".claude", "skills")
_CODEX_TOOL_COMMAND_RE = re.compile(
    r"(?:codex_tool_runner\.py|app\.services\.codex_tool_runner)",
    flags=re.IGNORECASE,
)
_SHELL_ARG_RE_TEMPLATE = r"--{name}(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))"
_DIRECT_TOOL_MAX_STEPS = 18
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
                "- In After Effects JSX, create captions, subtitles, title cards, and dense CJK text with comp.layers.addBoxText(); never assign TextDocument.boxText because it is read-only, and center rendered bounds with sourceRectAtTime().",
                "- Once a session already has an active project, treat it as the default target for follow-up edits and renders.",
                "- Use run_after_effects_jsx only for creative edits that are not already covered by the higher-level Shotwright tools.",
                "- If run_after_effects_jsx fails after a project was created, fix the JSX and retry against the same project_id; do not create a replacement workspace unless the project tool reports that the workspace is unrecoverable.",
                "- In After Effects JSX, guard every layer, property, and effect lookup before use; unsupported effects and missing properties often return null or undefined.",
                "- In After Effects JSX, do not call setValue() on animated properties that may already have keyframes; remove existing keys first or use setValueAtTime()/setValueAtKey().",
                "- For normal Shotwright creative work, do not use arbitrary shell commands unless the user explicitly asks for repository/debug work or every relevant Shotwright tool has failed.",
                "- Do not override the container image unless the user explicitly asks for a different image.",
                "- When rendering succeeds, mention the preview stream and export archive when relevant.",
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

        active_project_id = str(session_doc.get("active_project_id") or "").strip()
        if not active_project_id:
            return f"User request:\n{content}"

        project_doc = await project_collection.find_one({"_id": active_project_id, "session_id": app_session_id})
        if not project_doc:
            return f"User request:\n{content}"

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
                "",
                "User request:",
                content,
            ]
        )
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
                "- Never call shell_command, never ask to run codex_tool_runner.py, and never claim a render/export exists until a tool result confirms it.",
                "- For tool_call, set tool_name to exactly one available Shotwright tool and arguments_json to a JSON object string for that tool.",
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
            raise ValueError(f"Codex Shotwright tool arguments_json must be a JSON object string: {arguments_json[:500]}") from exc
        if not isinstance(arguments, dict):
            raise ValueError("Codex Shotwright tool arguments_json must decode to a JSON object.")
        parsed["arguments"] = arguments
        parsed["tool_name"] = str(parsed.get("tool_name") or "").strip()
        parsed["response"] = str(parsed.get("response") or "").strip()
        return parsed

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
        return "\n".join(
            [
                "Shotwright backend executed your requested tool call.",
                f"Tool: {tool_name}",
                "Result JSON:",
                json.dumps(payload, ensure_ascii=False, indent=2),
                "",
                "Continue using the Direct Shotwright tool protocol. Return the next tool_call if more work is required; otherwise return final.",
            ]
        )

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

        for step_index in range(1, _DIRECT_TOOL_MAX_STEPS + 1):
            result = await client.run_turn(
                input=current_input,
                thread_id=current_thread_id,
                working_directory=str(runtime_settings.get("codex_workspace_root") or ""),
                model=model,
                model_reasoning_effort=reasoning_effort,
                approval_policy=str(runtime_settings.get("codex_approval_policy") or ""),
                sandbox_mode=str(runtime_settings.get("codex_sandbox_mode") or ""),
                network_access_enabled=bool(runtime_settings.get("codex_network_access_enabled")),
                skip_git_repo_check=bool(runtime_settings.get("codex_skip_git_repo_check")),
                web_search_mode=str(runtime_settings.get("codex_web_search_mode") or ""),
                config=codex_sdk_config or None,
                output_schema=_DIRECT_TOOL_PLAN_SCHEMA,
                on_event=on_event,
            )
            current_thread_id = result.thread_id or current_thread_id
            plan = self._parse_direct_tool_plan(result.final_response)
            action = str(plan["action"])
            if action == "final":
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
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        assistant_text = item.get("text") if item.get("type") == "agent_message" else None
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
                thread_id = _first_non_empty(
                    session_doc.get("agent_thread_id"),
                    session_doc.get("codex_thread_id"),
                )
                local_profile = load_local_codex_profile()
                codex_sdk_config = build_codex_sdk_config(local_profile)

                await self._persist_event(
                    app_session_id,
                    "session.turn.started",
                    {
                        "provider": "codex",
                        "thread_id": thread_id or None,
                        "attachment_count": len(persisted_attachments),
                        "attachment_mime_types": [
                            attachment.get("mime_type") for attachment in persisted_attachments
                        ],
                        "timeout_seconds": turn_timeout_seconds,
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

                result = await asyncio.wait_for(
                    self._run_direct_tool_loop(
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
                    ),
                    timeout=turn_timeout_seconds,
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
            except TimeoutError:
                timeout_message = f"Shotwright timed out waiting for this Codex turn after {turn_timeout_seconds:g} seconds."
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
