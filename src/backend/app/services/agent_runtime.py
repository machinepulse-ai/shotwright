"""Provider-aware agent runtime facade."""

from __future__ import annotations

from app.database import get_admin_collection, get_session_collection
from app.models.session import ReasoningEffort
from app.services.codex_config import resolve_agent_provider
from app.services.codex_runtime import runtime_manager as codex_runtime_manager
from app.services.copilot_runtime import runtime_manager as copilot_runtime_manager


class ShotwrightAgentRuntimeManager:
    async def _active_provider(self) -> str:
        doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
        return resolve_agent_provider(doc)

    async def get_active_provider(self) -> str:
        return await self._active_provider()

    def _provider_runtime(self, provider: str):
        return codex_runtime_manager if provider == "codex" else copilot_runtime_manager

    async def ensure_repo_skills_available(self) -> None:
        await copilot_runtime_manager.ensure_repo_skills_available()
        await codex_runtime_manager.ensure_repo_skills_available()

    async def shutdown(self) -> None:
        await copilot_runtime_manager.shutdown()
        await codex_runtime_manager.shutdown()

    async def get_runtime_settings(self) -> dict:
        provider = await self._active_provider()
        return await self._provider_runtime(provider).get_runtime_settings()

    async def resolve_turn_timeout_seconds(self) -> float:
        provider = await self._active_provider()
        return await self._provider_runtime(provider).resolve_turn_timeout_seconds()

    async def resolve_default_session_settings(self) -> tuple[str, ReasoningEffort | None]:
        provider = await self._active_provider()
        return await self._provider_runtime(provider).resolve_default_session_settings()

    async def list_available_models(self, force_refresh: bool = False) -> list[dict]:
        provider = await self._active_provider()
        return await self._provider_runtime(provider).list_available_models(force_refresh=force_refresh)

    async def validate_model_choice(
        self,
        model_id: str,
        reasoning_effort: str | None,
    ) -> tuple[str, ReasoningEffort | None]:
        provider = await self._active_provider()
        return await self._provider_runtime(provider).validate_model_choice(model_id, reasoning_effort)

    async def apply_session_settings(
        self,
        app_session_id: str,
        model_id: str,
        reasoning_effort: ReasoningEffort | None,
    ) -> None:
        provider = await self._active_provider()
        await self._provider_runtime(provider).apply_session_settings(app_session_id, model_id, reasoning_effort)

    async def send_message(self, app_session_id: str, content: str, attachments: list[dict] | None = None) -> dict:
        provider = await self._active_provider()
        inactive_runtime = copilot_runtime_manager if provider == "codex" else codex_runtime_manager
        await inactive_runtime.disconnect_session(app_session_id)
        await get_session_collection().update_one(
            {"_id": app_session_id},
            {"$set": {"agent_provider": provider}},
        )
        return await self._provider_runtime(provider).send_message(app_session_id, content, attachments)

    async def cancel_turn(self, app_session_id: str) -> bool:
        provider = await self._active_provider()
        cancelled = await self._provider_runtime(provider).cancel_turn(app_session_id)
        if cancelled:
            return True
        other_runtime = copilot_runtime_manager if provider == "codex" else codex_runtime_manager
        return await other_runtime.cancel_turn(app_session_id)

    async def disconnect_session(self, app_session_id: str) -> None:
        await copilot_runtime_manager.disconnect_session(app_session_id)
        await codex_runtime_manager.disconnect_session(app_session_id)

    async def reconcile_session_status(self, app_session_id: str, session_doc: dict | None = None) -> dict | None:
        provider = str((session_doc or {}).get("agent_provider") or await self._active_provider())
        runtime = self._provider_runtime(provider)
        return await runtime.reconcile_session_status(app_session_id, session_doc)


runtime_manager = ShotwrightAgentRuntimeManager()
