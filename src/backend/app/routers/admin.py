"""Admin panel router — login, token management, dashboard."""

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.database import get_admin_collection, get_container_collection, get_session_collection
from app.middleware.auth import create_access_token, require_admin, verify_password
from app.models.admin import AdminLogin, AdminSettings, GithubTokenUpdate, TokenResponse

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
    doc = await col.find_one({"_id": "settings"})
    return AdminSettings(github_token_set=bool(doc and doc.get("github_token")))


@router.put("/github-token", dependencies=[Depends(require_admin)])
async def update_github_token(body: GithubTokenUpdate):
    col = get_admin_collection()
    await col.update_one(
        {"_id": "settings"},
        {"$set": {"github_token": body.github_token}},
        upsert=True,
    )
    return {"ok": True}


@router.get("/dashboard", dependencies=[Depends(require_admin)])
async def admin_dashboard():
    sessions_col = get_session_collection()
    containers_col = get_container_collection()

    total_sessions = await sessions_col.count_documents({})
    active_sessions = await sessions_col.count_documents({"status": "active"})
    total_containers = await containers_col.count_documents({})
    running_containers = await containers_col.count_documents({"status": "running"})

    return {
        "total_sessions": total_sessions,
        "active_sessions": active_sessions,
        "total_containers": total_containers,
        "running_containers": running_containers,
    }
