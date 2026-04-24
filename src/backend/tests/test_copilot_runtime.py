import asyncio
from types import SimpleNamespace

import pytest

from app.services import copilot_runtime as module
from app.services.copilot_runtime import runtime_manager


class DummyRuntime:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.pending_tasks: set[asyncio.Task] = set()
        self.turn_state = None
        self.event_sequence = 0
        self.sent_messages: list[dict] = []
        self.workspace_root = ""
        self.session = SimpleNamespace(send=self._send)

    async def _send(self, content: str, attachments=None) -> str:
        self.sent_messages.append({"content": content, "attachments": attachments})
        return "copilot-message-id"

    def next_event_sequence(self) -> int:
        self.event_sequence += 1
        return self.event_sequence


class DummyRuntimeCancelled(DummyRuntime):
    async def _send(self, content: str, attachments=None) -> str:
        self.sent_messages.append({"content": content, "attachments": attachments})

        async def _cancel_turn() -> None:
            await asyncio.sleep(0)
            assert self.turn_state is not None
            self.turn_state.finalized = True
            self.turn_state.error = module.TurnCancelledError("Generation stopped by user.")
            self.turn_state.idle_event.set()

        asyncio.create_task(_cancel_turn())
        return "copilot-message-id"


class DummyRuntimeCompleted(DummyRuntime):
    async def _send(self, content: str, attachments=None) -> str:
        self.sent_messages.append({"content": content, "attachments": attachments})

        async def _complete_turn() -> None:
            await asyncio.sleep(0)
            assert self.turn_state is not None
            self.turn_state.content = "done"
            self.turn_state.idle_event.set()

        asyncio.create_task(_complete_turn())
        return "copilot-message-id"


class DummyRuntimeSilentCompleted(DummyRuntime):
    async def _send(self, content: str, attachments=None) -> str:
        self.sent_messages.append({"content": content, "attachments": attachments})

        async def _complete_turn() -> None:
            await asyncio.sleep(0)
            assert self.turn_state is not None
            self.turn_state.finalized = True
            self.turn_state.idle_event.set()

        asyncio.create_task(_complete_turn())
        return "copilot-message-id"


class DummyRuntimeHanging(DummyRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.send_started = asyncio.Event()
        self.send_cancelled = asyncio.Event()

    async def _send(self, content: str, attachments=None) -> str:
        self.sent_messages.append({"content": content, "attachments": attachments})
        self.send_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.send_cancelled.set()
            raise


def test_system_prompt_warns_against_duplicate_project_recovery() -> None:
    prompt = runtime_manager._system_prompt()

    assert "keep using that same managed workspace" in prompt
    assert "do not switch into repository inspection" in prompt
    assert "treat it as the default target for later creative turns" in prompt


class FakeFindOneCollection:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    async def find_one(self, query: dict) -> dict | None:
        for doc in self._docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None


class FakeSdkSession:
    def __init__(self, session_id: str = "copilot-session-1") -> None:
        self.session_id = session_id
        self.on_callback = None

    def on(self, callback):
        self.on_callback = callback
        return lambda: None


class ScriptedFakeSdkSession(FakeSdkSession):
    def __init__(self, session_id: str = "copilot-session-1", script: list[dict] | None = None) -> None:
        super().__init__(session_id)
        self.script = script or []
        self.sent_messages: list[dict] = []

    async def send(self, content: str, attachments=None) -> str:
        self.sent_messages.append({"content": content, "attachments": attachments})

        async def _emit_events() -> None:
            for entry in self.script:
                delay = float(entry.get("delay", 0) or 0)
                if delay > 0:
                    await asyncio.sleep(delay)
                assert self.on_callback is not None
                data = entry.get("data")
                if isinstance(data, dict):
                    data = SimpleNamespace(**data)
                elif data is None:
                    data = SimpleNamespace()
                self.on_callback(
                    SimpleNamespace(
                        type=entry["type"],
                        data=data,
                    )
                )

        asyncio.create_task(_emit_events())
        return "copilot-message-id"


class FakeCopilotClient:
    def __init__(self, session_factory=None) -> None:
        self.started = False
        self.created_session_id = None
        self.created_session = None
        self.create_kwargs = None
        self._session_factory = session_factory or FakeSdkSession

    async def start(self) -> None:
        self.started = True

    async def create_session(self, session_id: str, **kwargs):
        self.created_session_id = session_id
        self.create_kwargs = kwargs
        self.created_session = self._session_factory(session_id)
        return self.created_session


@pytest.mark.asyncio
async def test_ensure_repo_skill_bundle_hydrates_repo_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    monkeypatch.setattr(module, "_REPO_ROOT", repo_root)

    def fake_ensure_skills_bundle(*, source_repo_root, install_root, proxy=None, github_token=None, log=None):
        skill_root = install_root / ".github" / "skills" / "after-effects-scripting-guide"
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(
            "---\nname: after-effects-scripting-guide\ndescription: Use when testing skill loading.\n---\n",
            encoding="utf-8",
        )
        return {
            "artifactVersion": "0.0.1",
            "skillsRoot": str(install_root / ".github" / "skills"),
            "status": "downloaded",
        }

    monkeypatch.setattr(module, "_load_skills_bundle_helpers", lambda: (RuntimeError, fake_ensure_skills_bundle))

    await runtime_manager._ensure_repo_skill_bundle(
        {
            "copilot_workspace_root": str(tmp_path / "workspace"),
            "copilot_http_proxy": "http://proxy.internal:8080",
            "copilot_https_proxy": "",
            "copilot_no_proxy": "",
            "copilot_use_logged_in_user": False,
            "copilot_cli_path": "",
        },
        "token",
    )

    assert (repo_root / ".github" / "skills" / "after-effects-scripting-guide" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_build_runtime_turn_content_injects_active_project_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module,
        "get_session_collection",
        lambda: FakeFindOneCollection([
            {"_id": "session-1", "active_project_id": "project-1"},
        ]),
    )
    monkeypatch.setattr(
        module,
        "get_project_collection",
        lambda: FakeFindOneCollection([
            {
                "_id": "project-1",
                "session_id": "session-1",
                "filename": "round1.aep",
                "entry_aep_file": "round1.aep",
                "compositions": [{"name": "Main"}],
            }
        ]),
    )
    monkeypatch.setattr(
        module.nr,
        "list_render_outputs",
        lambda session_id, limit=None: [{"filename": "round1.mp4"}],
    )

    runtime_content = await runtime_manager._build_runtime_turn_content("session-1", "把背景改成黑色")

    assert "Active project id: project-1" in runtime_content
    assert "Active project file: round1.aep" in runtime_content
    assert "Known compositions: Main" in runtime_content
    assert "Recent render outputs: round1.mp4" in runtime_content
    assert runtime_content.endswith("把背景改成黑色")


@pytest.mark.asyncio
async def test_build_runtime_passes_skill_directories_to_copilot_sdk(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    repo_skill_root = repo_root / ".github" / "skills" / "nexrender-cli-export"
    repo_skill_root.mkdir(parents=True)
    (repo_skill_root / "SKILL.md").write_text(
        "---\nname: nexrender-cli-export\ndescription: Use bundled repo skills.\n---\n",
        encoding="utf-8",
    )
    workspace_root = tmp_path / "workspace"
    skill_root = workspace_root / ".github" / "skills" / "after-effects-scripting-guide"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: after-effects-scripting-guide\ndescription: Use when testing skill loading.\n---\n",
        encoding="utf-8",
    )

    fake_client = FakeCopilotClient()
    monkeypatch.setattr(module, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(runtime_manager, "_repo_skills_hydration_task", None, raising=False)
    monkeypatch.setattr(runtime_manager, "_repo_skills_hydration_last_attempt_at", 0.0, raising=False)
    monkeypatch.setattr(runtime_manager, "_prime_repo_skill_bundle", lambda runtime_settings, github_token: None)
    monkeypatch.setattr(
        runtime_manager,
        "_workspace_root_candidates",
        lambda configured_workspace_root: [str(workspace_root), str(repo_root)],
    )

    monkeypatch.setattr(
        runtime_manager,
        "_resolve_runtime_settings",
        lambda: asyncio.sleep(
            0,
            result={
                "copilot_cli_path": "",
                "copilot_workspace_root": str(workspace_root),
                "copilot_use_logged_in_user": False,
                "copilot_http_proxy": "",
                "copilot_https_proxy": "",
                "copilot_no_proxy": "",
            },
        ),
    )
    monkeypatch.setattr(runtime_manager, "_resolve_github_token", lambda: asyncio.sleep(0, result="token"))
    monkeypatch.setattr(runtime_manager, "_build_client", lambda github_token, runtime_settings: fake_client)
    monkeypatch.setattr(runtime_manager, "resolve_default_session_settings", lambda: asyncio.sleep(0, result=("gpt-5.4", "high")))
    monkeypatch.setattr(module, "build_shotwright_tools", lambda app_session_id: ["tool"])
    monkeypatch.setattr(
        module,
        "get_session_collection",
        lambda: FakeFindOneCollection([
            {
                "_id": "session-skill-test",
                "status": "idle",
            }
        ]),
    )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        return {"_id": app_session_id, "status": status, **extra}

    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)

    runtime = await runtime_manager._build_runtime("session-skill-test")

    assert fake_client.started is True
    assert fake_client.created_session_id == "shotwright-session-skill-test"
    assert fake_client.create_kwargs is not None
    assert fake_client.create_kwargs["working_directory"] == str(workspace_root)
    assert str(workspace_root / ".github" / "skills") in fake_client.create_kwargs["skill_directories"]
    assert str(repo_root / ".github" / "skills") in fake_client.create_kwargs["skill_directories"]
    assert "after-effects-scripting-guide" in fake_client.create_kwargs["custom_agents"][0]["skills"]
    assert "nexrender-cli-export" in fake_client.create_kwargs["custom_agents"][0]["skills"]
    assert runtime.workspace_root == str(workspace_root)


@pytest.mark.asyncio
async def test_build_runtime_does_not_block_on_repo_skill_hydration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)

    fake_client = FakeCopilotClient()
    hydration_started = asyncio.Event()
    hydration_can_finish = asyncio.Event()

    monkeypatch.setattr(module, "_REPO_ROOT", repo_root)

    async def fake_ensure_repo_skill_bundle(runtime_settings: dict[str, str | bool], github_token: str | None) -> None:
        hydration_started.set()
        await hydration_can_finish.wait()

    monkeypatch.setattr(runtime_manager, "_ensure_repo_skill_bundle", fake_ensure_repo_skill_bundle)
    monkeypatch.setattr(runtime_manager, "_repo_skills_hydration_task", None, raising=False)
    monkeypatch.setattr(runtime_manager, "_repo_skills_hydration_last_attempt_at", 0.0, raising=False)
    monkeypatch.setattr(
        runtime_manager,
        "_workspace_root_candidates",
        lambda configured_workspace_root: [str(workspace_root), str(repo_root)],
    )
    monkeypatch.setattr(
        runtime_manager,
        "_resolve_runtime_settings",
        lambda: asyncio.sleep(
            0,
            result={
                "copilot_cli_path": "",
                "copilot_workspace_root": str(workspace_root),
                "copilot_use_logged_in_user": False,
                "copilot_http_proxy": "",
                "copilot_https_proxy": "",
                "copilot_no_proxy": "",
            },
        ),
    )
    monkeypatch.setattr(runtime_manager, "_resolve_github_token", lambda: asyncio.sleep(0, result="token"))
    monkeypatch.setattr(runtime_manager, "_build_client", lambda github_token, runtime_settings: fake_client)
    monkeypatch.setattr(runtime_manager, "resolve_default_session_settings", lambda: asyncio.sleep(0, result=("gpt-5.4", "high")))
    monkeypatch.setattr(module, "build_shotwright_tools", lambda app_session_id: ["tool"])
    monkeypatch.setattr(
        module,
        "get_session_collection",
        lambda: FakeFindOneCollection([
            {
                "_id": "session-hydration-pending-test",
                "status": "idle",
            }
        ]),
    )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        return {"_id": app_session_id, "status": status, **extra}

    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)

    runtime = await asyncio.wait_for(runtime_manager._build_runtime("session-hydration-pending-test"), timeout=1)

    assert fake_client.started is True
    assert fake_client.create_kwargs is not None
    assert fake_client.create_kwargs["skill_directories"] == []
    assert "skills" not in fake_client.create_kwargs["custom_agents"][0]
    assert runtime.workspace_root == str(workspace_root)

    hydration_task = runtime_manager._repo_skills_hydration_task
    assert hydration_task is not None
    await asyncio.wait_for(hydration_started.wait(), timeout=1)

    hydration_can_finish.set()
    await asyncio.wait_for(hydration_task, timeout=1)
    assert runtime_manager._repo_skills_hydration_task is None


@pytest.mark.asyncio
async def test_assistant_message_event_sets_idle_when_session_idle_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    repo_skill_root = repo_root / ".github" / "skills" / "nexrender-cli-export"
    repo_skill_root.mkdir(parents=True, exist_ok=True)
    (repo_skill_root / "SKILL.md").write_text(
        "---\nname: nexrender-cli-export\ndescription: Use bundled repo skills.\n---\n",
        encoding="utf-8",
    )
    workspace_root = tmp_path / "workspace"
    skill_root = workspace_root / ".github" / "skills" / "after-effects-scripting-guide"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: after-effects-scripting-guide\ndescription: Use when testing skill loading.\n---\n",
        encoding="utf-8",
    )

    fake_client = FakeCopilotClient()
    monkeypatch.setattr(module, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(runtime_manager, "_repo_skills_hydration_task", None, raising=False)
    monkeypatch.setattr(runtime_manager, "_repo_skills_hydration_last_attempt_at", 0.0, raising=False)
    monkeypatch.setattr(
        runtime_manager,
        "_workspace_root_candidates",
        lambda configured_workspace_root: [str(workspace_root), str(repo_root)],
    )
    monkeypatch.setattr(
        runtime_manager,
        "_resolve_runtime_settings",
        lambda: asyncio.sleep(
            0,
            result={
                "copilot_cli_path": "",
                "copilot_workspace_root": str(workspace_root),
                "copilot_use_logged_in_user": False,
                "copilot_http_proxy": "",
                "copilot_https_proxy": "",
                "copilot_no_proxy": "",
            },
        ),
    )
    monkeypatch.setattr(runtime_manager, "_resolve_github_token", lambda: asyncio.sleep(0, result="token"))
    monkeypatch.setattr(runtime_manager, "_build_client", lambda github_token, runtime_settings: fake_client)
    monkeypatch.setattr(runtime_manager, "resolve_default_session_settings", lambda: asyncio.sleep(0, result=("gpt-5.4", "high")))
    monkeypatch.setattr(module, "build_shotwright_tools", lambda app_session_id: ["tool"])
    monkeypatch.setattr(
        module,
        "get_session_collection",
        lambda: FakeFindOneCollection([
            {
                "_id": "session-event-test",
                "status": "running",
            }
        ]),
    )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        return None

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        return {
            "_id": turn_state.message_id,
            "content": content,
            "metadata": {
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }

    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)

    runtime = await runtime_manager._build_runtime("session-event-test")
    monkeypatch.setattr(runtime_manager, "_runtimes", {"session-event-test": runtime})

    runtime.turn_state = module._StreamingTurnState(
        "assistant-doc",
        turn_id="turn-1",
        user_message_id="user-1",
    )

    assert fake_client.created_session is not None
    assert fake_client.created_session.on_callback is not None

    fake_client.created_session.on_callback(
        SimpleNamespace(
            type="assistant.message",
            data=SimpleNamespace(content="render finished"),
        )
    )
    await asyncio.sleep(0)

    assert runtime.turn_state.finalized is True
    assert runtime.turn_state.content == "render finished"
    assert runtime.turn_state.idle_event.is_set() is True

    if runtime.pending_tasks:
        await asyncio.gather(*list(runtime.pending_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_send_message_uses_augmented_runtime_content(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = DummyRuntimeCompleted()
    assistant_doc_holder: dict[str, dict] = {}

    async def fake_ensure_runtime(app_session_id: str):
        return runtime

    async def fake_build_runtime_turn_content(app_session_id: str, content: str) -> str:
        return f"Shotwright session context:\n- Active project id: project-1\n\nUser request:\n{content}"

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        return None

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        assistant_doc_holder["doc"] = {
            **assistant_doc_holder["doc"],
            "content": content,
            "metadata": {
                **assistant_doc_holder["doc"].get("metadata", {}),
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }
        return assistant_doc_holder["doc"]

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "_build_runtime_turn_content", fake_build_runtime_turn_content)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
    monkeypatch.setattr(module, "get_message_collection", lambda: FakeMessageCollection(assistant_doc_holder))

    result = await runtime_manager.send_message("session-followup", "把背景改成黑色")

    assert result["session_status"] == "idle"
    assert runtime.sent_messages[0]["content"].startswith("Shotwright session context:")
    assert runtime.sent_messages[0]["content"].endswith("把背景改成黑色")


@pytest.mark.asyncio
async def test_send_message_synthesizes_success_text_when_model_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = DummyRuntimeSilentCompleted()
    assistant_doc_holder: dict[str, dict] = {}

    async def fake_ensure_runtime(app_session_id: str):
        return runtime

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        return None

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        assistant_doc_holder["doc"] = {
            **assistant_doc_holder["doc"],
            "content": content,
            "metadata": {
                **assistant_doc_holder["doc"].get("metadata", {}),
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }
        return assistant_doc_holder["doc"]

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
    monkeypatch.setattr(module, "get_message_collection", lambda: FakeMessageCollection(assistant_doc_holder))

    result = await runtime_manager.send_message("session-silent-success", "hello")

    assert result["session_status"] == "idle"
    assert result["assistant_message"]["content"] == (
        "Shotwright completed the requested work. Inspect the updated session state for the active project, renders, or other artifacts."
    )
    assert result["assistant_message"]["metadata"]["state"] == "completed"


@pytest.mark.asyncio
async def test_send_message_ignores_empty_assistant_message_until_real_reply_arrives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)

    fake_client = FakeCopilotClient(
        session_factory=lambda session_id: ScriptedFakeSdkSession(
            session_id,
            script=[
                {"type": "assistant.message", "data": {"content": ""}, "delay": 0},
                {"type": "assistant.message_delta", "data": {"delta_content": "先看运动路径，再把跟踪数据应用到空对象。"}, "delay": 0},
                {"type": "assistant.message", "data": {"content": "先看运动路径，再把跟踪数据应用到空对象。"}, "delay": 0},
            ],
        )
    )
    assistant_doc_holder: dict[str, dict] = {}

    monkeypatch.setattr(module, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(runtime_manager, "_repo_skills_hydration_task", None, raising=False)
    monkeypatch.setattr(runtime_manager, "_repo_skills_hydration_last_attempt_at", 0.0, raising=False)
    monkeypatch.setattr(
        runtime_manager,
        "_workspace_root_candidates",
        lambda configured_workspace_root: [str(workspace_root), str(repo_root)],
    )
    monkeypatch.setattr(
        runtime_manager,
        "_resolve_runtime_settings",
        lambda: asyncio.sleep(
            0,
            result={
                "copilot_cli_path": "",
                "copilot_workspace_root": str(workspace_root),
                "copilot_use_logged_in_user": False,
                "copilot_http_proxy": "",
                "copilot_https_proxy": "",
                "copilot_no_proxy": "",
            },
        ),
    )
    monkeypatch.setattr(runtime_manager, "_resolve_github_token", lambda: asyncio.sleep(0, result="token"))
    monkeypatch.setattr(runtime_manager, "_build_client", lambda github_token, runtime_settings: fake_client)
    monkeypatch.setattr(runtime_manager, "resolve_default_session_settings", lambda: asyncio.sleep(0, result=("gpt-5.4", "high")))
    monkeypatch.setattr(module, "build_shotwright_tools", lambda app_session_id: ["tool"])
    monkeypatch.setattr(
        module,
        "get_session_collection",
        lambda: FakeFindOneCollection([
            {
                "_id": "session-empty-assistant-message",
                "status": "idle",
            }
        ]),
    )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        return None

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        assistant_doc_holder["doc"] = {
            **assistant_doc_holder["doc"],
            "content": content,
            "metadata": {
                **assistant_doc_holder["doc"].get("metadata", {}),
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }
        return assistant_doc_holder["doc"]

    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=1.0))
    monkeypatch.setattr(module, "get_message_collection", lambda: FakeMessageCollection(assistant_doc_holder))

    runtime = await runtime_manager._build_runtime("session-empty-assistant-message")
    monkeypatch.setattr(runtime_manager, "_runtimes", {"session-empty-assistant-message": runtime})
    monkeypatch.setattr(runtime_manager, "ensure_runtime", lambda app_session_id: asyncio.sleep(0, result=runtime))

    result = await runtime_manager.send_message("session-empty-assistant-message", "如果用jsx脚本做呢")

    assert result["session_status"] == "idle"
    assert result["assistant_message"]["content"] == "先看运动路径，再把跟踪数据应用到空对象。"
    assert result["assistant_message"]["metadata"]["state"] == "completed"


@pytest.mark.asyncio
async def test_reconcile_session_status_marks_stale_running_sessions_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = DummyRuntime()
    captured = {
        "statuses": [],
        "disconnects": 0,
    }

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        captured["statuses"].append({"app_session_id": app_session_id, "status": status, "extra": extra})
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_disconnect_session(app_session_id: str) -> None:
        captured["disconnects"] += 1

    monkeypatch.setattr(runtime_manager, "_runtimes", {"session-stale": runtime})
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "disconnect_session", fake_disconnect_session)

    reconciled = await runtime_manager.reconcile_session_status(
        "session-stale",
        {"_id": "session-stale", "status": "running", "last_error": None},
    )

    assert reconciled["status"] == "error"
    assert "active Copilot turn state" in reconciled["last_error"]
    assert captured["statuses"] == [
        {
            "app_session_id": "session-stale",
            "status": "error",
            "extra": {"last_error": reconciled["last_error"]},
        }
    ]
    assert captured["disconnects"] == 1


@pytest.mark.asyncio
async def test_reconcile_session_status_keeps_live_running_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = DummyRuntime()
    runtime.turn_state = SimpleNamespace(finalized=False)
    captured = {"statuses": 0, "disconnects": 0}

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        captured["statuses"] += 1
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_disconnect_session(app_session_id: str) -> None:
        captured["disconnects"] += 1

    monkeypatch.setattr(runtime_manager, "_runtimes", {"session-live": runtime})
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "disconnect_session", fake_disconnect_session)

    session_doc = {"_id": "session-live", "status": "running", "last_error": None}
    reconciled = await runtime_manager.reconcile_session_status("session-live", session_doc)

    assert reconciled == session_doc
    assert captured == {"statuses": 0, "disconnects": 0}


class FakeMessageCollection:
    def __init__(self, assistant_doc_holder: dict[str, dict]) -> None:
        self._assistant_doc_holder = assistant_doc_holder

    async def find_one(self, query: dict) -> dict:
        return self._assistant_doc_holder["doc"]


@pytest.mark.asyncio
async def test_send_message_timeout_returns_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = DummyRuntime()
    assistant_doc_holder: dict[str, dict] = {}
    captured = {
        "events": [],
        "statuses": [],
        "sync_calls": [],
        "disconnects": 0,
    }

    async def fake_ensure_runtime(app_session_id: str):
        return runtime

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        captured["events"].append(
            {
                "type": event_type,
                "payload": payload,
                "turn_id": turn_id,
                "sequence": sequence,
            }
        )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        captured["statuses"].append({"status": status, "extra": extra})
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        assistant_doc_holder["doc"] = {
            **assistant_doc_holder["doc"],
            "content": content,
            "metadata": {
                **assistant_doc_holder["doc"].get("metadata", {}),
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }
        captured["sync_calls"].append(assistant_doc_holder["doc"])
        return assistant_doc_holder["doc"]

    async def fake_disconnect_session(app_session_id: str) -> None:
        captured["disconnects"] += 1

    monkeypatch.setattr(module.settings, "copilot_turn_timeout_seconds", 0.01)
    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=0.01))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
    monkeypatch.setattr(runtime_manager, "disconnect_session", fake_disconnect_session)
    monkeypatch.setattr(module, "get_message_collection", lambda: FakeMessageCollection(assistant_doc_holder))

    result = await runtime_manager.send_message("session-timeout-test", "hello")

    assert result["session_status"] == "error"
    assert result["assistant_message"]["content"] == "Shotwright timed out waiting for this turn after 0.01 seconds."
    assert result["assistant_message"]["metadata"]["state"] == "error"
    assert captured["disconnects"] == 1
    assert captured["events"][-1]["type"] == "session.timeout"
    assert captured["events"][-1]["payload"]["partial_output"] is False
    assert captured["statuses"][-1]["extra"]["last_error"] == "Shotwright timed out waiting for this turn after 0.01 seconds."


@pytest.mark.asyncio
async def test_send_message_interruption_returns_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = DummyRuntime()
    assistant_doc_holder: dict[str, dict] = {}
    captured = {
        "events": [],
        "statuses": [],
        "disconnects": 0,
    }

    async def fake_ensure_runtime(app_session_id: str):
        return runtime

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        captured["events"].append(
            {
                "type": event_type,
                "payload": payload,
                "turn_id": turn_id,
                "sequence": sequence,
            }
        )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        captured["statuses"].append({"status": status, "extra": extra})
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        assistant_doc_holder["doc"] = {
            **assistant_doc_holder["doc"],
            "content": content,
            "metadata": {
                **assistant_doc_holder["doc"].get("metadata", {}),
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }
        return assistant_doc_holder["doc"]

    async def fake_disconnect_session(app_session_id: str) -> None:
        captured["disconnects"] += 1

    async def fake_wait_for(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.CancelledError()

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
    monkeypatch.setattr(runtime_manager, "disconnect_session", fake_disconnect_session)
    monkeypatch.setattr(module, "get_message_collection", lambda: FakeMessageCollection(assistant_doc_holder))
    monkeypatch.setattr(module.asyncio, "wait_for", fake_wait_for)

    result = await runtime_manager.send_message("session-interrupted-test", "hello")

    assert result["session_status"] == "error"
    assert result["assistant_message"]["content"] == (
        "Shotwright interrupted this turn because the API worker restarted or the request disconnected."
    )
    assert result["assistant_message"]["metadata"]["state"] == "error"
    assert captured["disconnects"] == 1
    assert captured["events"][-1]["type"] == "session.error"
    assert captured["events"][-1]["payload"]["interrupted"] is True
    assert captured["statuses"][-1]["extra"]["last_error"] == (
        "Shotwright interrupted this turn because the API worker restarted or the request disconnected."
    )


@pytest.mark.asyncio
async def test_send_message_cancel_returns_idle_response(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = DummyRuntimeCancelled()
    assistant_doc_holder: dict[str, dict] = {}
    captured = {
        "events": [],
        "statuses": [],
        "disconnects": 0,
    }

    async def fake_ensure_runtime(app_session_id: str):
        return runtime

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        captured["events"].append(
            {
                "type": event_type,
                "payload": payload,
                "turn_id": turn_id,
                "sequence": sequence,
            }
        )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        captured["statuses"].append({"status": status, "extra": extra})
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        assistant_doc_holder["doc"] = {
            **assistant_doc_holder["doc"],
            "content": content,
            "metadata": {
                **assistant_doc_holder["doc"].get("metadata", {}),
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }
        return assistant_doc_holder["doc"]

    async def fake_disconnect_session(app_session_id: str) -> None:
        captured["disconnects"] += 1

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
    monkeypatch.setattr(runtime_manager, "disconnect_session", fake_disconnect_session)
    monkeypatch.setattr(module, "get_message_collection", lambda: FakeMessageCollection(assistant_doc_holder))

    result = await runtime_manager.send_message("session-cancel-test", "hello")

    assert result["session_status"] == "idle"
    assert result["assistant_message"]["content"] == "Generation stopped by user."
    assert result["assistant_message"]["metadata"]["state"] == "cancelled"
    assert captured["disconnects"] == 1
    assert captured["events"][-1]["type"] == "session.cancelled"
    assert captured["events"][-1]["payload"]["partial_output"] is False
    assert captured["statuses"][-1] == {"status": "idle", "extra": {"last_error": None}}


@pytest.mark.asyncio
async def test_cancel_turn_cancels_active_send_task_and_returns_cancelled_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DummyRuntimeHanging()
    assistant_doc_holder: dict[str, dict] = {}
    captured = {
        "events": [],
        "statuses": [],
        "disconnects": 0,
    }

    async def fake_ensure_runtime(app_session_id: str):
        return runtime

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        captured["events"].append(
            {
                "type": event_type,
                "payload": payload,
                "turn_id": turn_id,
                "sequence": sequence,
            }
        )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        captured["statuses"].append({"status": status, "extra": extra})
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        assistant_doc_holder["doc"] = {
            **assistant_doc_holder["doc"],
            "content": content,
            "metadata": {
                **assistant_doc_holder["doc"].get("metadata", {}),
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }
        return assistant_doc_holder["doc"]

    async def fake_disconnect_session(app_session_id: str) -> None:
        captured["disconnects"] += 1

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
    monkeypatch.setattr(runtime_manager, "disconnect_session", fake_disconnect_session)
    monkeypatch.setattr(runtime_manager, "_runtimes", {"session-cancel-test": runtime})
    monkeypatch.setattr(module, "get_message_collection", lambda: FakeMessageCollection(assistant_doc_holder))

    send_task = asyncio.create_task(runtime_manager.send_message("session-cancel-test", "hello"))

    await asyncio.wait_for(runtime.send_started.wait(), timeout=1)

    cancelled = await runtime_manager.cancel_turn("session-cancel-test")
    result = await asyncio.wait_for(send_task, timeout=1)

    assert cancelled is True
    assert runtime.send_cancelled.is_set()
    assert result["session_status"] == "idle"
    assert result["assistant_message"]["content"] == "Generation stopped by user."
    assert result["assistant_message"]["metadata"]["state"] == "cancelled"
    assert captured["disconnects"] == 1
    assert captured["events"][-1]["type"] == "session.cancelled"
    assert captured["statuses"][-1] == {"status": "idle", "extra": {"last_error": None}}


@pytest.mark.asyncio
async def test_cancel_turn_cancels_pending_runtime_setup_and_returns_cancelled_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant_doc_holder: dict[str, dict] = {}
    captured = {
        "events": [],
        "statuses": [],
    }
    setup_started = asyncio.Event()

    async def fake_ensure_runtime(app_session_id: str):
        setup_started.set()
        await asyncio.Future()

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        captured["events"].append(
            {
                "type": event_type,
                "payload": payload,
                "turn_id": turn_id,
                "sequence": sequence,
            }
        )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        captured["statuses"].append({"status": status, "extra": extra})
        return {"_id": app_session_id, "status": status, **extra}

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)

    send_task = asyncio.create_task(runtime_manager.send_message("session-bootstrap-cancel", "hello"))

    await asyncio.wait_for(setup_started.wait(), timeout=1)

    cancelled = await runtime_manager.cancel_turn("session-bootstrap-cancel")
    result = await asyncio.wait_for(send_task, timeout=1)

    assert cancelled is True
    assert result["session_status"] == "idle"
    assert result["assistant_message"]["content"] == "Generation stopped by user."
    assert result["assistant_message"]["metadata"]["state"] == "cancelled"
    assert captured["events"][-1]["type"] == "session.cancelled"
    assert captured["statuses"][-1] == {"status": "idle", "extra": {"last_error": None}}


@pytest.mark.asyncio
async def test_send_message_interruption_during_runtime_setup_returns_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant_doc_holder: dict[str, dict] = {}
    captured = {
        "events": [],
        "statuses": [],
    }
    setup_started = asyncio.Event()

    async def fake_ensure_runtime(app_session_id: str):
        setup_started.set()
        await asyncio.Future()

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        captured["events"].append(
            {
                "type": event_type,
                "payload": payload,
                "turn_id": turn_id,
                "sequence": sequence,
            }
        )

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        captured["statuses"].append({"status": status, "extra": extra})
        return {"_id": app_session_id, "status": status, **extra}

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)

    send_task = asyncio.create_task(runtime_manager.send_message("session-bootstrap-interrupted", "hello"))

    await asyncio.wait_for(setup_started.wait(), timeout=1)
    send_task.cancel()
    result = await asyncio.wait_for(send_task, timeout=1)

    assert result["session_status"] == "error"
    assert result["assistant_message"]["content"] == (
        "Shotwright interrupted this turn because the API worker restarted or the request disconnected."
    )
    assert result["assistant_message"]["metadata"]["state"] == "error"
    assert captured["events"][-1]["type"] == "session.error"
    assert captured["events"][-1]["payload"]["interrupted"] is True
    assert captured["statuses"][-1] == {
        "status": "error",
        "extra": {"last_error": "Shotwright interrupted this turn because the API worker restarted or the request disconnected."},
    }


@pytest.mark.asyncio
async def test_send_message_saves_inline_images_to_shared_uploads(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    runtime = DummyRuntimeCompleted()
    assistant_doc_holder: dict[str, dict] = {}
    captured_user_docs: list[dict] = []
    captured_context_refreshes: list[dict] = []

    async def fake_ensure_runtime(app_session_id: str):
        return runtime

    async def fake_persist_message(app_session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        doc = {
            "_id": f"{role}-doc",
            "session_id": app_session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        if role == "assistant":
            assistant_doc_holder["doc"] = doc
        else:
            captured_user_docs.append(doc)
        return doc

    async def fake_persist_event(app_session_id: str, event_type: str, payload: dict, turn_id: str | None = None, sequence: int | None = None) -> None:
        return None

    async def fake_set_session_status(app_session_id: str, status: str, **extra) -> dict:
        return {"_id": app_session_id, "status": status, **extra}

    async def fake_sync_streaming_message(turn_state, *, content: str, version: int, streaming: bool, state: str) -> dict:
        assistant_doc_holder["doc"] = {
            **assistant_doc_holder["doc"],
            "content": content,
            "metadata": {
                **assistant_doc_holder["doc"].get("metadata", {}),
                "streaming": streaming,
                "state": state,
                "version": version,
            },
        }
        return assistant_doc_holder["doc"]

    async def fake_publish_context_refresh(app_session_id: str, reason: str, **payload) -> None:
        captured_context_refreshes.append(
            {
                "session_id": app_session_id,
                "reason": reason,
                "payload": payload,
            }
        )

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
    monkeypatch.setattr(module, "publish_context_refresh", fake_publish_context_refresh)
    monkeypatch.setattr(module, "get_message_collection", lambda: FakeMessageCollection(assistant_doc_holder))
    monkeypatch.setattr(module.settings, "upload_dir", str(tmp_path))

    result = await runtime_manager.send_message(
        "session-image-test",
        "describe image",
        [
            {
                "type": "image",
                "mime_type": "image/png",
                "data_url": "data:image/png;base64,QUJD",
                "display_name": "reference-image.png",
                "width": 1,
                "height": 1,
                "size_bytes": 3,
            }
        ],
    )

    assert result["session_status"] == "idle"
    assert runtime.sent_messages

    sent_attachments = runtime.sent_messages[0]["attachments"]
    assert sent_attachments is not None
    assert [attachment["type"] for attachment in sent_attachments] == ["blob", "file"]

    file_attachment = sent_attachments[1]
    assert file_attachment["path"].endswith("reference-image.png")
    assert tmp_path in module.Path(file_attachment["path"]).parents
    assert module.Path(file_attachment["path"]).read_bytes() == b"ABC"

    persisted_attachment = captured_user_docs[0]["metadata"]["attachments"][0]
    assert persisted_attachment["file_path"] == file_attachment["path"]
    assert persisted_attachment["shared_relative_path"].startswith("session-image-test/")
    assert persisted_attachment["workspace_relative_path"].endswith("reference-image.png")
    assert persisted_attachment["display_name"] == "reference-image.png"
    assert captured_context_refreshes == [
        {
            "session_id": "session-image-test",
            "reason": "attachments.updated",
            "payload": {"image_attachment_count": 1},
        }
    ]