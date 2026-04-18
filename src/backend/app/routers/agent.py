"""Copilot agent action dispatcher."""

from fastapi import APIRouter, HTTPException, UploadFile

from app.database import get_event_collection, get_message_collection, get_project_collection, get_session_collection
from app.models.chat import ChatMessage, ChatTurnCreate, ChatTurnResult, SessionEvent
from app.models.container import ContainerInfo
from app.models.project import ProjectInfo
from app.models.session import SessionInDB
from app.services.copilot_runtime import runtime_manager
from app.services import container_manager as cm
from app.services import project_manager as pm

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/sessions/{session_id}/messages", response_model=ChatTurnResult)
async def send_chat_turn(session_id: str, body: ChatTurnCreate):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")
    result = await runtime_manager.send_message(session_id, body.content)
    return result


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessage])
async def list_messages(session_id: str):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")
    return await get_message_collection().find({"session_id": session_id}).sort("created_at", 1).to_list(length=500)


@router.get("/sessions/{session_id}/events", response_model=list[SessionEvent])
async def list_events(session_id: str, limit: int = 200):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")
    return await get_event_collection().find({"session_id": session_id}).sort("created_at", 1).to_list(length=limit)


@router.post("/sessions/{session_id}/uploads", response_model=ProjectInfo)
async def upload_session_project(session_id: str, file: UploadFile):
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip files accepted")
    payload = await file.read()
    if len(payload) > 500 * 1024 * 1024:
        raise HTTPException(413, "File too large")
    project = await pm.upload_project(session_id, payload, file.filename)
    return project


@router.get("/sessions/{session_id}/context")
async def get_agent_context(session_id: str):
    session_doc = await get_session_collection().find_one({"_id": session_id})
    if not session_doc:
        raise HTTPException(404, "Session not found")

    container_doc = None
    if session_doc.get("container_id"):
        container_doc = await cm.get_container(session_doc["container_id"])

    project_docs = await get_project_collection().find({"session_id": session_id}).sort("created_at", -1).to_list(length=100)
    return {
        "session": SessionInDB.model_validate(session_doc).model_dump(by_alias=True),
        "container": ContainerInfo.model_validate(container_doc).model_dump(by_alias=True) if container_doc else None,
        "projects": [ProjectInfo.model_validate(doc).model_dump(by_alias=True) for doc in project_docs],
        "latest_render_path": session_doc.get("latest_render_path"),
        "latest_render_url": f"/api/streams/renders/{session_id}" if session_doc.get("latest_render_path") else None,
        "latest_stream_url": session_doc.get("latest_stream_url"),
    }
