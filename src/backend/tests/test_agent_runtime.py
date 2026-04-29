from __future__ import annotations

import pytest

from app.services import agent_runtime


class FakeAdminCollection:
    def __init__(self, doc: dict) -> None:
        self.doc = doc

    async def find_one(self, query: dict) -> dict:
        return self.doc


class FakeSessionCollection:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def update_one(self, query: dict, update: dict):
        self.updates.append({"query": query, "update": update})


class FakeRuntime:
    def __init__(self, name: str) -> None:
        self.name = name
        self.disconnected: list[str] = []
        self.sent: list[dict] = []

    async def resolve_default_session_settings(self):
        return f"{self.name}-model", "high"

    async def send_message(self, app_session_id: str, content: str, attachments=None):
        self.sent.append({"session_id": app_session_id, "content": content, "attachments": attachments})
        return {"assistant_message": {"_id": "message-1"}, "session_status": "idle"}

    async def disconnect_session(self, app_session_id: str) -> None:
        self.disconnected.append(app_session_id)


@pytest.mark.asyncio
async def test_agent_runtime_dispatches_active_codex_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_admin = FakeAdminCollection({"_id": "settings", "agent_provider": "codex"})
    fake_sessions = FakeSessionCollection()
    fake_copilot = FakeRuntime("copilot")
    fake_codex = FakeRuntime("codex")
    monkeypatch.setattr(agent_runtime, "get_admin_collection", lambda: fake_admin)
    monkeypatch.setattr(agent_runtime, "get_session_collection", lambda: fake_sessions)
    monkeypatch.setattr(agent_runtime, "copilot_runtime_manager", fake_copilot)
    monkeypatch.setattr(agent_runtime, "codex_runtime_manager", fake_codex)

    manager = agent_runtime.ShotwrightAgentRuntimeManager()

    default_model, default_reasoning = await manager.resolve_default_session_settings()
    result = await manager.send_message("session-1", "render")

    assert (default_model, default_reasoning) == ("codex-model", "high")
    assert result["session_status"] == "idle"
    assert fake_codex.sent == [{"session_id": "session-1", "content": "render", "attachments": None}]
    assert fake_copilot.disconnected == ["session-1"]
    assert fake_sessions.updates[0]["update"]["$set"]["agent_provider"] == "codex"
