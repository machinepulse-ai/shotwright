"""Async Python client for the in-container Node Codex bridge."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_BRIDGE_SCRIPT = _REPO_ROOT / "src" / "backend" / "codex-bridge" / "bridge.mjs"

CodexBridgeEventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class CodexBridgeError(RuntimeError):
    """Raised when the Node Codex bridge fails or returns an error record."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        stderr: str = "",
        record: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.record = record or {}


@dataclass(frozen=True)
class CodexBridgeTurnResult:
    thread_id: str | None
    final_response: str
    usage: dict[str, Any] | None
    events: list[dict[str, Any]]


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def default_bridge_script_path() -> Path:
    configured_path = _first_non_empty(settings.codex_bridge_script)
    return Path(configured_path) if configured_path else _DEFAULT_BRIDGE_SCRIPT


def build_codex_input(content: str, attachments: list[dict[str, Any]] | None = None) -> str | list[dict[str, str]]:
    image_paths = [
        str(attachment.get("file_path") or "").strip()
        for attachment in attachments or []
        if isinstance(attachment, dict) and str(attachment.get("file_path") or "").strip()
    ]
    if not image_paths:
        return content

    input_items: list[dict[str, str]] = [{"type": "text", "text": content}]
    input_items.extend({"type": "local_image", "path": path} for path in image_paths)
    return input_items


def _build_child_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    proxy_values = {
        "http_proxy": _first_non_empty(
            extra_env.get("http_proxy") if extra_env else None,
            extra_env.get("HTTP_PROXY") if extra_env else None,
            settings.codex_http_proxy,
            os.environ.get("http_proxy"),
            os.environ.get("HTTP_PROXY"),
        ),
        "https_proxy": _first_non_empty(
            extra_env.get("https_proxy") if extra_env else None,
            extra_env.get("HTTPS_PROXY") if extra_env else None,
            settings.codex_https_proxy,
            os.environ.get("https_proxy"),
            os.environ.get("HTTPS_PROXY"),
        ),
        "no_proxy": _first_non_empty(
            extra_env.get("no_proxy") if extra_env else None,
            extra_env.get("NO_PROXY") if extra_env else None,
            settings.codex_no_proxy,
            os.environ.get("no_proxy"),
            os.environ.get("NO_PROXY"),
        ),
    }
    for lower_key, value in proxy_values.items():
        if not value:
            continue
        env[lower_key] = value
        env[lower_key.upper()] = value

    openai_api_key = _first_non_empty(settings.openai_api_key, os.environ.get("OPENAI_API_KEY"))
    if openai_api_key:
        env["OPENAI_API_KEY"] = openai_api_key

    if extra_env:
        env.update({key: value for key, value in extra_env.items() if isinstance(value, str)})
    return env


class CodexBridgeClient:
    """Spawn the Node bridge for one Codex turn and collect JSONL events."""

    def __init__(
        self,
        *,
        node_binary: str | None = None,
        bridge_script: str | Path | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        codex_path_override: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.node_binary = _first_non_empty(node_binary, settings.codex_node_path, "node")
        self.bridge_script = Path(bridge_script) if bridge_script else default_bridge_script_path()
        self.api_key = _first_non_empty(api_key, settings.openai_api_key, os.environ.get("OPENAI_API_KEY"))
        self.base_url = _first_non_empty(base_url, settings.codex_base_url)
        self.codex_path_override = _first_non_empty(codex_path_override, settings.codex_path_override)
        self.extra_env = extra_env or {}

    def build_request(
        self,
        *,
        input: str | list[dict[str, str]],
        thread_id: str | None = None,
        working_directory: str | None = None,
        model: str | None = None,
        model_reasoning_effort: str | None = None,
        approval_policy: str | None = None,
        sandbox_mode: str | None = None,
        network_access_enabled: bool | None = None,
        skip_git_repo_check: bool | None = None,
        web_search_mode: str | None = None,
        output_schema: dict[str, Any] | None = None,
        additional_directories: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "command": "run_turn",
            "input": input,
            "working_directory": _first_non_empty(
                working_directory,
                settings.codex_workspace_root,
                settings.copilot_workspace_root,
                str(_REPO_ROOT),
            ),
            "model": _first_non_empty(model, settings.codex_model, settings.copilot_model),
            "model_reasoning_effort": _first_non_empty(
                model_reasoning_effort,
                settings.codex_reasoning_effort,
                settings.copilot_reasoning_effort,
            ),
            "approval_policy": _first_non_empty(approval_policy, settings.codex_approval_policy, "never"),
            "sandbox_mode": _first_non_empty(sandbox_mode, settings.codex_sandbox_mode, "workspace-write"),
            "network_access_enabled": (
                settings.codex_network_access_enabled
                if network_access_enabled is None
                else bool(network_access_enabled)
            ),
            "skip_git_repo_check": (
                settings.codex_skip_git_repo_check
                if skip_git_repo_check is None
                else bool(skip_git_repo_check)
            ),
        }

        optional_string_values = {
            "thread_id": thread_id,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "codex_path_override": self.codex_path_override,
            "web_search_mode": _first_non_empty(web_search_mode, settings.codex_web_search_mode),
        }
        for key, value in optional_string_values.items():
            if value:
                request[key] = value

        if output_schema is not None:
            request["output_schema"] = output_schema
        if additional_directories:
            request["additional_directories"] = additional_directories
        if config is not None:
            request["config"] = config

        bridge_env = _build_child_env(self.extra_env)
        request["env"] = {
            key: value
            for key, value in bridge_env.items()
            if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "OPENAI_API_KEY"}
            or key.lower() in {"http_proxy", "https_proxy", "no_proxy"}
        }
        return request

    async def run_turn(
        self,
        *,
        input: str | list[dict[str, str]],
        thread_id: str | None = None,
        working_directory: str | None = None,
        model: str | None = None,
        model_reasoning_effort: str | None = None,
        approval_policy: str | None = None,
        sandbox_mode: str | None = None,
        network_access_enabled: bool | None = None,
        skip_git_repo_check: bool | None = None,
        web_search_mode: str | None = None,
        output_schema: dict[str, Any] | None = None,
        additional_directories: list[str] | None = None,
        config: dict[str, Any] | None = None,
        on_event: CodexBridgeEventHandler | None = None,
    ) -> CodexBridgeTurnResult:
        request = self.build_request(
            input=input,
            thread_id=thread_id,
            working_directory=working_directory,
            model=model,
            model_reasoning_effort=model_reasoning_effort,
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
            network_access_enabled=network_access_enabled,
            skip_git_repo_check=skip_git_repo_check,
            web_search_mode=web_search_mode,
            output_schema=output_schema,
            additional_directories=additional_directories,
            config=config,
        )
        return await self._run_request(request, on_event=on_event)

    async def health(self) -> bool:
        result = await self._run_request({"command": "health"}, on_event=None)
        return result.final_response == "" and not result.events

    async def _run_request(
        self,
        request: dict[str, Any],
        *,
        on_event: CodexBridgeEventHandler | None,
    ) -> CodexBridgeTurnResult:
        if not self.bridge_script.is_file():
            raise CodexBridgeError(f"Codex bridge script not found: {self.bridge_script}")

        process = await asyncio.create_subprocess_exec(
            self.node_binary,
            str(self.bridge_script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_child_env(self.extra_env),
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        stderr_task = asyncio.create_task(process.stderr.read())
        complete_record: dict[str, Any] | None = None
        error_record: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []

        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False).encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    record = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise CodexBridgeError(f"Codex bridge emitted invalid JSONL: {line!r}") from exc

                record_type = record.get("type")
                if record_type == "event":
                    event = record.get("event") or {}
                    events.append(event)
                    if on_event:
                        callback_result = on_event(event)
                        if inspect.isawaitable(callback_result):
                            await callback_result
                elif record_type == "complete":
                    complete_record = record
                elif record_type == "error":
                    error_record = record

            exit_code = await process.wait()
            stderr = (await stderr_task).decode("utf-8", errors="replace")

            if error_record:
                raise CodexBridgeError(
                    str(error_record.get("message") or "Codex bridge failed."),
                    exit_code=exit_code,
                    stderr=stderr,
                    record=error_record,
                )

            if exit_code != 0:
                raise CodexBridgeError(
                    f"Codex bridge exited with code {exit_code}.",
                    exit_code=exit_code,
                    stderr=stderr,
                )

            if complete_record is None:
                raise CodexBridgeError("Codex bridge exited without a complete record.", stderr=stderr)

            return CodexBridgeTurnResult(
                thread_id=complete_record.get("thread_id"),
                final_response=str(complete_record.get("final_response") or ""),
                usage=complete_record.get("usage") if isinstance(complete_record.get("usage"), dict) else None,
                events=events,
            )
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
