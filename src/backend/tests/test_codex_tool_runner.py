from __future__ import annotations

import pytest
from copilot.tools import Tool, ToolResult

from app.services import codex_tool_runner as module


def test_build_codex_tool_manifest_uses_shotwright_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_handler(_invocation):
        return ToolResult(text_result_for_llm="ok")

    monkeypatch.setattr(
        module,
        "build_shotwright_tools",
        lambda _session_id: [
            Tool(
                name="inspect_workspace",
                description="Inspect session state.",
                handler=fake_handler,
                parameters={"type": "object", "properties": {}},
                skip_permission=True,
            )
        ],
    )

    manifest = module.build_codex_tool_manifest("session-1")

    assert manifest == [
        {
            "name": "inspect_workspace",
            "description": "Inspect session state.",
            "parameters": {"type": "object", "properties": {}},
            "skip_permission": True,
        }
    ]


@pytest.mark.asyncio
async def test_run_codex_tool_invokes_existing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_handler(invocation):
        captured["session_id"] = invocation.session_id
        captured["tool_name"] = invocation.tool_name
        captured["arguments"] = invocation.arguments
        return ToolResult(text_result_for_llm="workspace ok", session_log="checked")

    monkeypatch.setattr(
        module,
        "build_shotwright_tools",
        lambda _session_id: [
            Tool(
                name="inspect_workspace",
                description="Inspect session state.",
                handler=fake_handler,
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    payload = await module.run_codex_tool(
        "session-1",
        "inspect_workspace",
        {"detail": True},
        tool_call_id="call-1",
    )

    assert captured == {
        "session_id": "session-1",
        "tool_name": "inspect_workspace",
        "arguments": {"detail": True},
    }
    assert payload["type"] == "shotwright_tool_result"
    assert payload["success"] is True
    assert payload["text_result_for_llm"] == "workspace ok"
    assert payload["session_log"] == "checked"


@pytest.mark.asyncio
async def test_run_codex_tool_reports_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "build_shotwright_tools", lambda _session_id: [])

    payload = await module.run_codex_tool("session-1", "missing_tool", {})

    assert payload["success"] is False
    assert payload["result_type"] == "failure"
    assert "Unknown Shotwright tool: missing_tool" in payload["text_result_for_llm"]
