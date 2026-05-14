from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import codex_runtime as module
from app.services.codex_bridge import CodexBridgeError, CodexBridgeTurnResult


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
    assert "Embedded SKILL.md excerpts" in content
    assert "Active project id: project-1" in content
    assert "Known compositions: Main" in content
    assert "inspect_workspace.creative_quality_policy" in content
    assert "inspect_workspace.subtitle_style_policy" in content
    assert "merely technically valid but visually weak" in content


def test_resolve_matching_skill_invocations_handles_explicit_and_generic_mentions(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".github" / "skills" / "after-effects-scripting-guide"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: after-effects-scripting-guide\n---\n", encoding="utf-8")

    runtime_manager = module.ShotwrightCodexRuntimeManager()
    runtime_settings = {"codex_workspace_root": str(tmp_path)}

    explicit = runtime_manager._resolve_matching_skill_invocations(
        runtime_settings,
        "Use after-effects-scripting-guide skill for this render.",
    )
    generic = runtime_manager._resolve_matching_skill_invocations(
        runtime_settings,
        "请使用仓库索引的 after effects skill 指南制作项目",
    )

    assert explicit == [
        {
            "name": "after-effects-scripting-guide",
            "path": str(skill_file),
            "directory": str(skill_dir),
        }
    ]
    assert generic == explicit


def test_parse_direct_tool_plan_repairs_mangled_cjk_regex_in_nested_arguments_json() -> None:
    runtime_manager = module.ShotwrightCodexRuntimeManager()
    bad_arguments_json = (
        '{"project_id":"project-1",'
        '"script_content":"if (/[\\x04e00-\\x09fff]/.test(txt)) return true;"}'
    )
    raw_response = json.dumps(
        {
            "action": "tool_call",
            "tool_name": "run_after_effects_jsx",
            "arguments_json": bad_arguments_json,
            "response": "patch subtitles",
        }
    )

    parsed = runtime_manager._parse_direct_tool_plan(raw_response)

    assert parsed["tool_name"] == "run_after_effects_jsx"
    assert parsed["arguments"]["project_id"] == "project-1"
    assert "\u4e00-\u9fff" in parsed["arguments"]["script_content"]
    assert "\x04" not in parsed["arguments"]["script_content"]
    assert "\tfff" not in parsed["arguments"]["script_content"]


def test_render_visual_review_followup_attaches_storyboard_images(tmp_path: Path) -> None:
    runtime_manager = module.ShotwrightCodexRuntimeManager()
    full_storyboard = tmp_path / "full-storyboard.jpg"
    subtitle_storyboard = tmp_path / "subtitle-zone.jpg"
    full_storyboard.write_bytes(b"full")
    subtitle_storyboard.write_bytes(b"subtitle")

    followup = runtime_manager._build_render_visual_review_followup(
        {"output_path": "C:/data/exports/session-1/demo.mp4"},
        {"storyboard_image_path": str(full_storyboard), "shared_relative_path": "_storyboards/full.jpg"},
        {
            "subtitle_zone_storyboard": {
                "storyboard_image_path": str(subtitle_storyboard),
                "shared_relative_path": "_storyboards/subtitle.jpg",
            },
            "analysis": {"black_caption_risk": False},
        },
    )

    assert isinstance(followup, list)
    assert followup[0]["type"] == "text"
    assert "weakest frame" in followup[0]["text"]
    assert [item["path"] for item in followup[1:]] == [str(full_storyboard), str(subtitle_storyboard)]


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
    assert persisted_events[2]["data"]["duration_seconds"] >= 0


@pytest.mark.asyncio
async def test_handle_codex_event_keeps_direct_final_for_timeout_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manager = module.ShotwrightCodexRuntimeManager()
    runtime = module._CodexRuntimeHandle("session-1")
    turn_state = module._CodexTurnState("message-1", turn_id="turn-1")

    async def fake_persist_event(*_args, **_kwargs):
        return None

    async def fake_persist_codex_tool_bridge_events(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_persist_codex_tool_bridge_events", fake_persist_codex_tool_bridge_events)

    await runtime_manager._handle_codex_event(
        "session-1",
        runtime,
        turn_state,
        {"type": "thread.started", "thread_id": "thread-1"},
    )
    await runtime_manager._handle_codex_event(
        "session-1",
        runtime,
        turn_state,
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(
                    {
                        "action": "final",
                        "tool_name": "",
                        "arguments_json": "{}",
                        "response": "done before bridge timeout",
                    }
                ),
            },
        },
    )

    assert turn_state.latest_thread_id == "thread-1"
    assert turn_state.direct_final_response == "done before bridge timeout"
    assert turn_state.content == ""


@pytest.mark.asyncio
async def test_direct_tool_loop_requires_storyboard_after_render(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_manager = module.ShotwrightCodexRuntimeManager()
    runtime = module._CodexRuntimeHandle("session-1")
    turn_state = module._CodexTurnState("message-1", turn_id="turn-1")
    persisted_events: list[dict] = []
    tool_calls: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self) -> None:
            self.responses = [
                {
                    "action": "tool_call",
                    "tool_name": "render_after_effects_project",
                    "arguments_json": "{}",
                    "response": "render",
                },
                {
                    "action": "final",
                    "tool_name": "",
                    "arguments_json": "{}",
                    "response": "done too early",
                },
                {
                    "action": "tool_call",
                    "tool_name": "generate_storyboard_from_reference_video",
                    "arguments_json": '{"reference_video_path":"C:/data/exports/session-1/demo.mp4"}',
                    "response": "review",
                },
                {
                    "action": "final",
                    "tool_name": "",
                    "arguments_json": "{}",
                    "response": "done after review",
                },
            ]

        async def run_turn(self, **_kwargs):
            response = self.responses.pop(0)
            return CodexBridgeTurnResult(
                thread_id="thread-1",
                final_response=json.dumps(response),
                usage=None,
                events=[],
            )

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

    async def fake_run_codex_tool(app_session_id, tool_name, arguments, *, tool_call_id=""):
        tool_calls.append((tool_name, arguments))
        if tool_name == "render_after_effects_project":
            return {
                "success": True,
                "result_type": "success",
                "text_result_for_llm": json.dumps({"output_path": "C:/data/exports/session-1/demo.mp4"}),
            }
        return {
            "success": True,
            "result_type": "success",
            "text_result_for_llm": json.dumps({"storyboard_image_path": "C:/data/uploads/session-1/_storyboards/demo.jpg"}),
        }

    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(module, "run_codex_tool", fake_run_codex_tool)

    result = await runtime_manager._run_direct_tool_loop(
        app_session_id="session-1",
        runtime=runtime,
        turn_state=turn_state,
        client=FakeClient(),
        runtime_content="make a render",
        persisted_attachments=[],
        thread_id=None,
        runtime_settings={
            "codex_workspace_root": "",
            "codex_approval_policy": "",
            "codex_sandbox_mode": "",
            "codex_network_access_enabled": False,
            "codex_skip_git_repo_check": True,
            "codex_web_search_mode": "",
        },
        session_doc={},
        codex_sdk_config={},
        on_event=None,
    )

    assert result.final_response == "done after review"
    assert [name for name, _args in tool_calls] == [
        "render_after_effects_project",
        "generate_storyboard_from_reference_video",
    ]
    assert tool_calls[1][1]["reference_video_path"] == "C:/data/exports/session-1/demo.mp4"
    assert [event["type"] for event in persisted_events] == [
        "tool.execution_start",
        "tool.execution_complete",
        "tool.execution_start",
        "tool.execution_complete",
    ]


@pytest.mark.asyncio
async def test_direct_tool_loop_blocks_final_when_subtitle_zone_quality_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manager = module.ShotwrightCodexRuntimeManager()
    runtime = module._CodexRuntimeHandle("session-1")
    turn_state = module._CodexTurnState("message-1", turn_id="turn-1")
    tool_calls: list[tuple[str, dict]] = []
    quality_reviews = [
        {
            "success": True,
            "blocking": True,
            "subtitle_zone_storyboard": {
                "storyboard_image_path": "C:/data/uploads/session-1/_storyboards/demo_subtitle_zone.jpg",
                "shared_relative_path": "session-1/_storyboards/demo_subtitle_zone.jpg",
            },
            "analysis": {
                "near_black_ratio": 0.28,
                "dark_ratio": 0.36,
                "black_caption_risk": True,
                "risk_reasons": ["near-black pixels dominate the focused storyboard region"],
            },
        },
        {
            "success": True,
            "blocking": False,
            "subtitle_zone_storyboard": {
                "storyboard_image_path": "C:/data/uploads/session-1/_storyboards/demo2_subtitle_zone.jpg",
                "shared_relative_path": "session-1/_storyboards/demo2_subtitle_zone.jpg",
            },
            "analysis": {
                "near_black_ratio": 0.04,
                "dark_ratio": 0.18,
                "black_caption_risk": False,
                "risk_reasons": [],
            },
        },
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.responses = [
                {
                    "action": "tool_call",
                    "tool_name": "render_after_effects_project",
                    "arguments_json": "{}",
                    "response": "render",
                },
                {
                    "action": "tool_call",
                    "tool_name": "generate_storyboard_from_reference_video",
                    "arguments_json": '{"reference_video_path":"C:/data/exports/session-1/demo.mp4"}',
                    "response": "review",
                },
                {
                    "action": "tool_call",
                    "tool_name": "run_after_effects_jsx",
                    "arguments_json": '{"script_content":"revise captions"}',
                    "response": "revise",
                },
                {
                    "action": "tool_call",
                    "tool_name": "render_after_effects_project",
                    "arguments_json": '{"output_name":"demo2.mp4"}',
                    "response": "render again",
                },
                {
                    "action": "final",
                    "tool_name": "",
                    "arguments_json": "{}",
                    "response": "done too early",
                },
                {
                    "action": "tool_call",
                    "tool_name": "generate_storyboard_from_reference_video",
                    "arguments_json": '{"reference_video_path":"C:/data/exports/session-1/demo2.mp4"}',
                    "response": "review again",
                },
                {
                    "action": "final",
                    "tool_name": "",
                    "arguments_json": "{}",
                    "response": "done after quality review",
                },
            ]
            self.inputs: list[object] = []

        async def run_turn(self, **kwargs):
            self.inputs.append(kwargs.get("input"))
            response = self.responses.pop(0)
            return CodexBridgeTurnResult(
                thread_id="thread-1",
                final_response=json.dumps(response),
                usage=None,
                events=[],
            )

    fake_client = FakeClient()

    async def fake_persist_event(*_args, **_kwargs):
        return None

    async def fake_run_codex_tool(app_session_id, tool_name, arguments, *, tool_call_id=""):
        tool_calls.append((tool_name, arguments))
        if tool_name == "render_after_effects_project":
            output_name = str(arguments.get("output_name") or "demo.mp4")
            return {
                "success": True,
                "result_type": "success",
                "text_result_for_llm": json.dumps({"output_path": f"C:/data/exports/session-1/{output_name}"}),
            }
        if tool_name == "generate_storyboard_from_reference_video":
            return {
                "success": True,
                "result_type": "success",
                "text_result_for_llm": json.dumps(
                    {"storyboard_image_path": "C:/data/uploads/session-1/_storyboards/demo.jpg"}
                ),
            }
        return {"success": True, "result_type": "success", "text_result_for_llm": "{}"}

    def fake_quality_review(*_args, **_kwargs):
        return quality_reviews.pop(0)

    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_review_render_storyboard_quality", fake_quality_review)
    monkeypatch.setattr(module, "run_codex_tool", fake_run_codex_tool)

    result = await runtime_manager._run_direct_tool_loop(
        app_session_id="session-1",
        runtime=runtime,
        turn_state=turn_state,
        client=fake_client,
        runtime_content="make a render",
        persisted_attachments=[],
        thread_id=None,
        runtime_settings={
            "codex_workspace_root": "",
            "codex_approval_policy": "",
            "codex_sandbox_mode": "",
            "codex_network_access_enabled": False,
            "codex_skip_git_repo_check": True,
            "codex_web_search_mode": "",
        },
        session_doc={},
        codex_sdk_config={},
        on_event=None,
    )

    assert result.final_response == "done after quality review"
    assert [name for name, _args in tool_calls] == [
        "render_after_effects_project",
        "generate_storyboard_from_reference_video",
        "run_after_effects_jsx",
        "render_after_effects_project",
        "generate_storyboard_from_reference_video",
    ]
    assert any("caption readability/design risk" in str(item) for item in fake_client.inputs)


@pytest.mark.asyncio
async def test_direct_tool_loop_recovers_when_codex_thread_becomes_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manager = module.ShotwrightCodexRuntimeManager()
    runtime = module._CodexRuntimeHandle("session-1")
    turn_state = module._CodexTurnState("message-1", turn_id="turn-1")
    persisted_events: list[dict] = []
    tool_calls: list[str] = []

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0
            self.thread_ids: list[str | None] = []
            self.inputs: list[object] = []

        async def run_turn(self, **kwargs):
            self.calls += 1
            self.thread_ids.append(kwargs.get("thread_id"))
            self.inputs.append(kwargs.get("input"))
            if self.calls == 1:
                raise CodexBridgeError(
                    "Codex Exec exited with code 1: We're currently experiencing high demand. "
                    "failed to record rollout items: thread stale-thread not found"
                )
            if self.calls == 2:
                response = {
                    "action": "tool_call",
                    "tool_name": "inspect_workspace",
                    "arguments_json": "{}",
                    "response": "recover state",
                }
            else:
                response = {
                    "action": "final",
                    "tool_name": "",
                    "arguments_json": "{}",
                    "response": "done after recovery",
                }
            return CodexBridgeTurnResult(
                thread_id="new-thread",
                final_response=json.dumps(response),
                usage=None,
                events=[],
            )

    fake_client = FakeClient()

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

    async def fake_run_codex_tool(app_session_id, tool_name, arguments, *, tool_call_id=""):
        tool_calls.append(tool_name)
        return {"success": True, "result_type": "success", "text_result_for_llm": "{}"}

    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(module, "run_codex_tool", fake_run_codex_tool)

    result = await runtime_manager._run_direct_tool_loop(
        app_session_id="session-1",
        runtime=runtime,
        turn_state=turn_state,
        client=fake_client,
        runtime_content="make a render",
        persisted_attachments=[],
        thread_id="stale-thread",
        runtime_settings={
            "codex_workspace_root": "",
            "codex_approval_policy": "",
            "codex_sandbox_mode": "",
            "codex_network_access_enabled": False,
            "codex_skip_git_repo_check": True,
            "codex_web_search_mode": "",
        },
        session_doc={},
        codex_sdk_config={},
        on_event=None,
    )

    assert result.final_response == "done after recovery"
    assert fake_client.thread_ids == ["stale-thread", None, "new-thread"]
    assert "Do not rely on prior Codex thread memory" in str(fake_client.inputs[1])
    assert [event["type"] for event in persisted_events if event["type"] == "codex.thread.recovered"] == [
        "codex.thread.recovered"
    ]
    assert tool_calls == ["inspect_workspace"]
