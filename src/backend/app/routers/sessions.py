"""Session CRUD router."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.database import get_event_collection, get_message_collection, get_project_collection, get_session_collection
from app.models.session import SessionCreate, SessionInDB, SessionStatus, SessionUpdate
from app.services import container_manager as cm
from app.services.copilot_runtime import runtime_manager

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionInDB, status_code=201)
async def create_session(body: SessionCreate):
    now = datetime.now(timezone.utc)
    doc = {
        "_id": str(uuid4()),
        "name": body.name,
        "status": SessionStatus.idle.value,
        "copilot_session_id": None,
        "container_id": None,
        "active_project_id": None,
        "latest_render_path": None,
        "latest_stream_url": None,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    col = get_session_collection()
    await col.insert_one(doc)
    return doc


@router.get("", response_model=list[SessionInDB])
async def list_sessions():
    col = get_session_collection()
    return await col.find().sort("created_at", -1).to_list(length=100)


@router.get("/{session_id}", response_model=SessionInDB)
async def get_session(session_id: str):
    col = get_session_collection()
    doc = await col.find_one({"_id": session_id})
    if not doc:
        raise HTTPException(404, "Session not found")
    return doc


@router.patch("/{session_id}", response_model=SessionInDB)
async def update_session(session_id: str, body: SessionUpdate):
    col = get_session_collection()
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(400, "Nothing to update")
    updates["updated_at"] = datetime.now(timezone.utc)
    result = await col.find_one_and_update(
        {"_id": session_id},
        {"$set": updates},
        return_document=True,
    )
    if not result:
        raise HTTPException(404, "Session not found")
    return result


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str):
    col = get_session_collection()
    doc = await col.find_one({"_id": session_id})
    if not doc:
        raise HTTPException(404, "Session not found")

    if doc.get("container_id"):
        await cm.remove_container(doc["container_id"])
    await runtime_manager.disconnect_session(session_id)
    await get_project_collection().delete_many({"session_id": session_id})
    await get_message_collection().delete_many({"session_id": session_id})
    await get_event_collection().delete_many({"session_id": session_id})
    result = await col.delete_one({"_id": session_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Session not found")
