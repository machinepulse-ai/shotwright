"""Copilot agent action dispatcher."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.database import get_event_collection, get_message_collection, get_project_collection, get_session_collection
from app.models.chat import ChatMessage, ChatTurnCreate, ChatTurnResult, SessionEvent
from app.models.container import ContainerInfo
from app.models.media import ReferenceVideoInfo, RenderOutputInfo, StoryboardInfo
from app.models.project import ProjectInfo
from app.models.session import SessionInDB
from app.services.copilot_runtime import runtime_manager
from app.services.session_streams import session_stream_broker
from app.services import container_manager as cm
from app.services import nexrender as nr
from app.services import project_manager as pm
from app.services import reference_media as rm

router = APIRouter(prefix="/agent", tags=["agent"])


def _format_sse_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/sessions/{session_id}/messages", response_model=ChatTurnResult)
async def send_chat_turn(session_id: str, body: ChatTurnCreate):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")
    result = await runtime_manager.send_message(
        session_id,
        body.content.strip(),
        [attachment.model_dump() for attachment in body.attachments],
    )
    return result


@router.post("/sessions/{session_id}/cancel")
async def cancel_chat_turn(session_id: str):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")
    cancelled = await runtime_manager.cancel_turn(session_id)
    return {"ok": True, "cancelled": cancelled}


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessage])
async def list_messages(session_id: str):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")
    return await get_message_collection().find({"session_id": session_id}).sort("created_at", 1).to_list(length=500)


@router.get("/sessions/{session_id}/events", response_model=list[SessionEvent])
async def list_events(session_id: str, limit: int = 1000):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")
    return await get_event_collection().find({"session_id": session_id}).sort("created_at", 1).to_list(length=limit)


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")

    queue = await session_stream_broker.subscribe(session_id)

    async def event_stream():
        try:
            yield _format_sse_event("session.ready", {"session_id": session_id})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                yield _format_sse_event(envelope["type"], envelope["payload"])
        finally:
            await session_stream_broker.unsubscribe(session_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/uploads", response_model=ProjectInfo)
async def upload_session_project(session_id: str, file: UploadFile):
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip files accepted")
    payload = await file.read()
    if len(payload) > 500 * 1024 * 1024:
        raise HTTPException(413, "File too large")
    project = await pm.upload_project(session_id, payload, file.filename)
    return project


@router.post("/sessions/{session_id}/reference-videos", response_model=ReferenceVideoInfo)
async def upload_reference_video(session_id: str, file: UploadFile):
    session = await get_session_collection().find_one({"_id": session_id})
    if not session:
        raise HTTPException(404, "Session not found")
    if not file.filename:
        raise HTTPException(400, "Reference video filename is required")

    payload = await file.read()
    try:
        return rm.upload_reference_video(session_id, payload, file.filename)
    except rm.ReferenceMediaUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = 413 if "500 MB" in message else 400
        raise HTTPException(status_code, message) from exc


@router.get("/sessions/{session_id}/context")
async def get_agent_context(session_id: str):
    session_doc = await get_session_collection().find_one({"_id": session_id})
    if not session_doc:
        raise HTTPException(404, "Session not found")
    session_doc = await runtime_manager.reconcile_session_status(session_id, session_doc) or session_doc

    container_doc = None
    if session_doc.get("container_id"):
        container_doc = await cm.get_container(session_doc["container_id"])

    project_docs = await get_project_collection().find({"session_id": session_id}).sort("created_at", -1).to_list(length=100)
    return {
        "session": SessionInDB.model_validate(session_doc).model_dump(by_alias=True),
        "container": ContainerInfo.model_validate(container_doc).model_dump(by_alias=True) if container_doc else None,
        "projects": [ProjectInfo.model_validate(doc).model_dump(by_alias=True) for doc in project_docs],
        "reference_videos": [
            ReferenceVideoInfo.model_validate(doc).model_dump() for doc in rm.list_reference_videos(session_id, limit=12)
        ],
        "storyboards": [
            StoryboardInfo.model_validate(doc).model_dump() for doc in rm.list_storyboards(session_id, limit=12)
        ],
        "render_outputs": [
            RenderOutputInfo.model_validate(doc).model_dump() for doc in nr.list_render_outputs(session_id, limit=12)
        ],
        "latest_render_path": session_doc.get("latest_render_path"),
        "latest_render_url": f"/api/streams/renders/{session_id}" if session_doc.get("latest_render_path") else None,
        "latest_stream_url": session_doc.get("latest_stream_url"),
    }
