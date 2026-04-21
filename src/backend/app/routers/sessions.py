"""Session CRUD router."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pymongo import ReturnDocument

from app.config import settings
from app.database import get_event_collection, get_message_collection, get_project_collection, get_session_collection
from app.models.session import CopilotModelOption, SessionCreate, SessionInDB, SessionStatus, SessionUpdate
from app.services import container_manager as cm
from app.services.copilot_runtime import runtime_manager
from app.services.session_streams import publish_session_updated

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionInDB, status_code=201)
async def create_session(body: SessionCreate):
    now = datetime.now(timezone.utc)
    default_model, default_reasoning_effort = await runtime_manager.resolve_default_session_settings()
    doc = {
        "_id": str(uuid4()),
        "name": body.name,
        "status": SessionStatus.idle.value,
        "copilot_model": default_model,
        "copilot_reasoning_effort": default_reasoning_effort,
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


@router.get("/model-options", response_model=list[CopilotModelOption])
async def list_model_options():
    return await runtime_manager.list_available_models()


@router.get("", response_model=list[SessionInDB])
async def list_sessions():
    col = get_session_collection()
    docs = await col.find().sort("created_at", -1).to_list(length=100)
    reconciled_docs: list[dict] = []
    for doc in docs:
        reconciled_docs.append(await runtime_manager.reconcile_session_status(doc["_id"], doc) or doc)
    return reconciled_docs


@router.get("/{session_id}", response_model=SessionInDB)
async def get_session(session_id: str):
    col = get_session_collection()
    doc = await col.find_one({"_id": session_id})
    if not doc:
        raise HTTPException(404, "Session not found")
    return await runtime_manager.reconcile_session_status(session_id, doc) or doc


@router.patch("/{session_id}", response_model=SessionInDB)
async def update_session(session_id: str, body: SessionUpdate):
    col = get_session_collection()
    current = await col.find_one({"_id": session_id})
    if not current:
        raise HTTPException(404, "Session not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "Nothing to update")

    if "copilot_model" in updates or "copilot_reasoning_effort" in updates:
        requested_model = (updates.get("copilot_model") or current.get("copilot_model") or settings.copilot_model).strip()
        requested_reasoning = (
            updates["copilot_reasoning_effort"]
            if "copilot_reasoning_effort" in updates
            else current.get("copilot_reasoning_effort")
        )

        try:
            normalized_model, normalized_reasoning = await runtime_manager.validate_model_choice(
                requested_model,
                requested_reasoning,
            )
            await runtime_manager.apply_session_settings(session_id, normalized_model, normalized_reasoning)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        updates["copilot_model"] = normalized_model
        updates["copilot_reasoning_effort"] = normalized_reasoning

    updates["updated_at"] = datetime.now(timezone.utc)
    result = await col.find_one_and_update(
        {"_id": session_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(404, "Session not found")
    await publish_session_updated(session_id, result)
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
