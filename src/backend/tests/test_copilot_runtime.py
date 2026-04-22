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


def test_system_prompt_warns_against_duplicate_project_recovery() -> None:
    prompt = runtime_manager._system_prompt()

    assert "keep using that same managed workspace" in prompt
    assert "do not switch into repository inspection" in prompt


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
async def test_send_message_saves_inline_images_to_shared_uploads(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    runtime = DummyRuntimeCompleted()
    assistant_doc_holder: dict[str, dict] = {}
    captured_user_docs: list[dict] = []

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

    monkeypatch.setattr(runtime_manager, "ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(runtime_manager, "resolve_turn_timeout_seconds", lambda: asyncio.sleep(0, result=900.0))
    monkeypatch.setattr(runtime_manager, "_persist_message", fake_persist_message)
    monkeypatch.setattr(runtime_manager, "_persist_event", fake_persist_event)
    monkeypatch.setattr(runtime_manager, "_set_session_status", fake_set_session_status)
    monkeypatch.setattr(runtime_manager, "_sync_streaming_message", fake_sync_streaming_message)
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