"""Codex-compatible command runner for Shotwright's Copilot tools.

The Codex TypeScript SDK does not expose the same in-process custom tool API as
the Copilot SDK. This module keeps one source of truth by executing the existing
``build_shotwright_tools`` tool objects through a small JSON CLI that Codex can
call from its shell.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from copilot.tools import ToolInvocation, ToolResult

from app.database import close_db, connect_db
from app.services.agent_tools import build_shotwright_tools

_TOOL_RUNNER_TYPE = "shotwright_tool_result"


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]
    if hasattr(value, "value"):
        return _serialize_value(value.value)
    if hasattr(value, "model_dump"):
        return _serialize_value(value.model_dump())
    if hasattr(value, "__dict__"):
        return {key: _serialize_value(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


def _stringify_for_llm(value: Any) -> str:
    serialized = _serialize_value(value)
    if isinstance(serialized, str):
        return serialized
    return json.dumps(serialized, ensure_ascii=False)


def _tool_result_to_payload(tool_name: str, result: ToolResult) -> dict[str, Any]:
    result_type = str(_serialize_value(getattr(result, "result_type", "success")) or "success")
    return {
        "type": _TOOL_RUNNER_TYPE,
        "tool_name": tool_name,
        "result_type": result_type,
        "success": result_type == "success",
        "text_result_for_llm": str(getattr(result, "text_result_for_llm", "") or ""),
        "error": getattr(result, "error", None),
        "session_log": getattr(result, "session_log", None),
        "binary_results_for_llm": _serialize_value(getattr(result, "binary_results_for_llm", None)),
        "tool_telemetry": _serialize_value(getattr(result, "tool_telemetry", None)),
    }


def build_codex_tool_manifest(app_session_id: str) -> list[dict[str, Any]]:
    """Return the Copilot tool definitions in a Codex prompt-friendly shape."""

    manifest: list[dict[str, Any]] = []
    for tool in build_shotwright_tools(app_session_id):
        manifest.append(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {"type": "object", "properties": {}},
                "skip_permission": bool(tool.skip_permission),
            }
        )
    return manifest


async def run_codex_tool(
    app_session_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    tool_call_id: str = "",
) -> dict[str, Any]:
    """Execute one Shotwright tool and return a JSON-serializable result."""

    tools = {tool.name: tool for tool in build_shotwright_tools(app_session_id)}
    tool = tools.get(tool_name)
    if tool is None:
        known_tools = ", ".join(sorted(tools))
        return {
            "type": _TOOL_RUNNER_TYPE,
            "tool_name": tool_name,
            "result_type": "failure",
            "success": False,
            "text_result_for_llm": f"Unknown Shotwright tool: {tool_name}. Available tools: {known_tools}",
            "error": f"Unknown Shotwright tool: {tool_name}",
            "session_log": None,
        }

    invocation = ToolInvocation(
        session_id=app_session_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments or {},
    )
    try:
        maybe_result = tool.handler(invocation)
        result = await maybe_result if inspect.isawaitable(maybe_result) else maybe_result
        if not isinstance(result, ToolResult):
            result = ToolResult(text_result_for_llm=_stringify_for_llm(result), result_type="success")
        return _tool_result_to_payload(tool_name, result)
    except Exception as exc:  # noqa: BLE001 - this is a CLI boundary for LLM tool calls.
        return {
            "type": _TOOL_RUNNER_TYPE,
            "tool_name": tool_name,
            "result_type": "failure",
            "success": False,
            "text_result_for_llm": str(exc),
            "error": str(exc),
            "session_log": None,
        }


def _load_arguments(args: argparse.Namespace) -> dict[str, Any]:
    raw_arguments = args.arguments_json
    if args.arguments_file:
        raw_arguments = Path(args.arguments_file).read_text(encoding="utf-8")
    elif raw_arguments == "@-":
        raw_arguments = sys.stdin.read()

    if not raw_arguments:
        return {}

    parsed = json.loads(raw_arguments)
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    return parsed


async def _main_async(args: argparse.Namespace) -> int:
    await connect_db()
    try:
        if args.list:
            print(json.dumps({"tools": build_codex_tool_manifest(args.session_id)}, ensure_ascii=False))
            return 0

        if not args.tool:
            raise ValueError("--tool is required unless --list is used.")

        payload = await run_codex_tool(
            args.session_id,
            args.tool,
            _load_arguments(args),
            tool_call_id=args.tool_call_id or "",
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload.get("success") else 2
    finally:
        await close_db()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Shotwright tools from the Codex bridge.")
    parser.add_argument("--session-id", required=True, help="Shotwright app session id.")
    parser.add_argument("--tool", help="Tool name to execute.")
    parser.add_argument("--tool-call-id", default="", help="Optional stable tool call id for tracing.")
    parser.add_argument("--arguments-json", default="{}", help="JSON object, or @- to read from stdin.")
    parser.add_argument("--arguments-file", default="", help="Path to a JSON file containing tool arguments.")
    parser.add_argument("--list", action="store_true", help="List available tools for the session.")
    return asyncio.run(_main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
