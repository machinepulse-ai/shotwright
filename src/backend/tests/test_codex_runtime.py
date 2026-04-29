from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import codex_runtime as module


class FakeFindOneCollection:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    async def find_one(self, query: dict, *args, **kwargs) -> dict | None:
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None


@pytest.mark.asyncio
async def test_build_runtime_turn_content_injects_tool_and_skill_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / ".github" / "skills" / "after-effects-scripting-guide"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: after-effects-scripting-guide\n---\n", encoding="utf-8")

    runtime_manager = module.ShotwrightCodexRuntimeManager()

    async def fake_runtime_settings() -> dict:
        return {
            "codex_workspace_root": str(tmp_path),
            "codex_http_proxy": "",
            "codex_https_proxy": "",
            "codex_no_proxy": "",
        }

    monkeypatch.setattr(runtime_manager, "_resolve_runtime_settings", fake_runtime_settings)
    monkeypatch.setattr(
        module,
        "get_session_collection",
        lambda: FakeFindOneCollection([{"_id": "session-1", "active_project_id": "project-1"}]),
    )
    monkeypatch.setattr(
        module,
        "get_project_collection",
        lambda: FakeFindOneCollection(
            [
                {
                    "_id": "project-1",
                    "session_id": "session-1",
                    "filename": "scene.aep",
                    "compositions": [{"name": "Main"}],
                }
            ]
        ),
    )
    monkeypatch.setattr(module.nr, "list_render_outputs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "build_codex_tool_manifest",
        lambda _session_id: [
            {
                "name": "inspect_workspace",
                "description": "Inspect session state.",
                "parameters": {"type": "object", "properties": {}},
                "skip_permission": True,
            }
        ],
    )

    content = await runtime_manager._build_runtime_turn_content("session-1", "渲染一版预览")

    assert "Shotwright tool compatibility bridge" in content
    assert "codex_tool_runner.py" in content
    assert '"name": "inspect_workspace"' in content
    assert "after-effects-scripting-guide" in content
    assert "Active project id: project-1" in content
    assert "Known compositions: Main" in content


@pytest.mark.asyncio
async def test_handle_codex_event_translates_tool_runner_command(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_manager = module.ShotwrightCodexRuntimeManager()
    runtime = module._CodexRuntimeHandle("session-1")
    turn_state = module._CodexTurnState("message-1", turn_id="turn-1")
    persisted_events: list[dict] = []

    async def fake_persist_event(app_session_id, event_type, data, *, turn_id=None, sequence=None):
        persisted_events.append(
            {
                "session_id": app_session_id,
                "type": event_type,
                "data": data,
                "turn_id": turn_id,
                "sequence": sequence,
            }
        )

    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)

    tool_payload = {
        "type": "shotwright_tool_result",
        "tool_name": "inspect_workspace",
        "result_type": "success",
        "success": True,
        "text_result_for_llm": "workspace ok",
        "session_log": "checked",
    }
    await runtime_manager._handle_codex_event(
        "session-1",
        runtime,
        turn_state,
        {
            "type": "item.completed",
            "item": {
                "id": "cmd-1",
                "type": "command_execution",
                "command": 'python codex_tool_runner.py --session-id "session-1" --tool "inspect_workspace"',
                "exit_code": 0,
                "output": json.dumps(tool_payload),
            },
        },
    )

    assert [event["type"] for event in persisted_events] == [
        "codex.item.completed",
        "tool.execution_start",
        "tool.execution_complete",
    ]
    assert persisted_events[1]["data"]["tool_name"] == "inspect_workspace"
    assert persisted_events[2]["data"]["success"] is True
    assert persisted_events[2]["data"]["text_result_for_llm"] == "workspace ok"
