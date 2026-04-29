"""Admin panel router — login, token management, and runtime settings."""

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.database import get_admin_collection, get_container_collection, get_session_collection
from app.middleware.auth import create_access_token, require_admin, verify_password
from app.models.admin import AdminLogin, AdminSettings, AgentSettingsUpdate, GithubTokenUpdate, OpenAIKeyUpdate, TokenResponse
from app.models.session import CopilotModelOption
from app.services.agent_runtime import runtime_manager
from app.services.codex_config import (
    is_openai_api_key_set,
    resolve_agent_provider,
    resolve_codex_runtime_settings,
)
from app.services.copilot_runtime import runtime_manager as copilot_runtime_manager

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/login", response_model=TokenResponse)
async def admin_login(body: AdminLogin):
    if not verify_password(body.password, settings.admin_password):
        raise HTTPException(401, "Invalid password")
    token = create_access_token({"sub": "admin"})
    return TokenResponse(access_token=token)


@router.get("/settings", response_model=AdminSettings, dependencies=[Depends(require_admin)])
async def get_admin_settings():
    col = get_admin_collection()
    doc = await col.find_one({"_id": "settings"}) or {}
    copilot_settings = await copilot_runtime_manager.get_runtime_settings()
    codex_settings = resolve_codex_runtime_settings(doc)
    return AdminSettings(
        agent_provider=resolve_agent_provider(doc),
        github_token_set=bool(doc.get("github_token")),
        openai_api_key_set=is_openai_api_key_set(doc),
        default_copilot_model=(doc.get("default_copilot_model") or settings.copilot_model),
        default_copilot_reasoning_effort=(
            doc["default_copilot_reasoning_effort"]
            if "default_copilot_reasoning_effort" in doc
            else settings.copilot_reasoning_effort
        ),
        copilot_turn_timeout_seconds=await copilot_runtime_manager.resolve_turn_timeout_seconds(),
        **copilot_settings,
        **codex_settings,
    )


@router.put("/settings", response_model=AdminSettings, dependencies=[Depends(require_admin)])
async def update_admin_settings(body: AgentSettingsUpdate):
    col = get_admin_collection()
    payload = body.model_dump()
    payload["default_copilot_model"] = payload["default_copilot_model"].strip() or settings.copilot_model
    payload["copilot_turn_timeout_seconds"] = float(payload["copilot_turn_timeout_seconds"])
    payload["codex_turn_timeout_seconds"] = float(payload["codex_turn_timeout_seconds"])
    payload["copilot_cli_path"] = payload["copilot_cli_path"].strip()
    payload["copilot_workspace_root"] = payload["copilot_workspace_root"].strip()
    payload["copilot_http_proxy"] = payload["copilot_http_proxy"].strip()
    payload["copilot_https_proxy"] = payload["copilot_https_proxy"].strip()
    payload["copilot_no_proxy"] = payload["copilot_no_proxy"].strip()
    payload["codex_node_path"] = payload["codex_node_path"].strip()
    payload["codex_bridge_script"] = payload["codex_bridge_script"].strip()
    payload["codex_path_override"] = payload["codex_path_override"].strip()
    payload["codex_base_url"] = payload["codex_base_url"].strip()
    payload["codex_model"] = payload["codex_model"].strip() or settings.codex_model
    payload["codex_workspace_root"] = payload["codex_workspace_root"].strip()
    payload["codex_approval_policy"] = payload["codex_approval_policy"].strip() or settings.codex_approval_policy
    payload["codex_sandbox_mode"] = payload["codex_sandbox_mode"].strip() or settings.codex_sandbox_mode
    payload["codex_web_search_mode"] = payload["codex_web_search_mode"].strip()
    payload["codex_http_proxy"] = payload["codex_http_proxy"].strip()
    payload["codex_https_proxy"] = payload["codex_https_proxy"].strip()
    payload["codex_no_proxy"] = payload["codex_no_proxy"].strip()
    await col.update_one({"_id": "settings"}, {"$set": payload}, upsert=True)
    await runtime_manager.shutdown()
    doc = await col.find_one({"_id": "settings"}) or {}
    copilot_settings = await copilot_runtime_manager.get_runtime_settings()
    codex_settings = resolve_codex_runtime_settings(doc)
    return AdminSettings(
        agent_provider=resolve_agent_provider(doc),
        github_token_set=bool(doc.get("github_token")),
        openai_api_key_set=is_openai_api_key_set(doc),
        default_copilot_model=(doc.get("default_copilot_model") or settings.copilot_model),
        default_copilot_reasoning_effort=(
            doc["default_copilot_reasoning_effort"]
            if "default_copilot_reasoning_effort" in doc
            else settings.copilot_reasoning_effort
        ),
        copilot_turn_timeout_seconds=await copilot_runtime_manager.resolve_turn_timeout_seconds(),
        **copilot_settings,
        **codex_settings,
    )


@router.put("/github-token", dependencies=[Depends(require_admin)])
async def update_github_token(body: GithubTokenUpdate):
    col = get_admin_collection()
    await col.update_one(
        {"_id": "settings"},
        {"$set": {"github_token": body.github_token.strip()}},
        upsert=True,
    )
    await runtime_manager.shutdown()
    return {"ok": True}


@router.put("/openai-api-key", dependencies=[Depends(require_admin)])
async def update_openai_api_key(body: OpenAIKeyUpdate):
    col = get_admin_collection()
    await col.update_one(
        {"_id": "settings"},
        {"$set": {"openai_api_key": body.openai_api_key.strip()}},
        upsert=True,
    )
    await runtime_manager.shutdown()
    return {"ok": True}


@router.get("/copilot-model-options", response_model=list[CopilotModelOption], dependencies=[Depends(require_admin)])
async def list_admin_copilot_model_options():
    try:
        return await copilot_runtime_manager.list_available_models()
    except ValueError as exc:
        if "GitHub token is not configured" in str(exc):
            return []
        raise


@router.get("/dashboard", dependencies=[Depends(require_admin)])
async def admin_dashboard():
    sessions_col = get_session_collection()
    containers_col = get_container_collection()

    total_sessions = await sessions_col.count_documents({})
    active_sessions = await sessions_col.count_documents({"status": {"$in": ["idle", "running", "awaiting_input"]}})
    total_containers = await containers_col.count_documents({})
    running_containers = await containers_col.count_documents({"status": "running"})

    return {
        "total_sessions": total_sessions,
        "active_sessions": active_sessions,
        "total_containers": total_containers,
        "running_containers": running_containers,
    }
