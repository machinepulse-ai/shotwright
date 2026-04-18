"""Session-scoped realtime event broker for the Shotwright UI."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.database import get_session_collection
from app.models.chat import ChatMessage, SessionEvent
from app.models.session import SessionInDB


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStreamBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers[session_id].add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(session_id, None)

    async def publish(self, session_id: str, event_type: str, payload: Any) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.get(session_id, set()))

        if not subscribers:
            return

        envelope = {
            "session_id": session_id,
            "type": event_type,
            "emitted_at": _utcnow_iso(),
            "payload": jsonable_encoder(payload),
        }
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                continue


session_stream_broker = SessionStreamBroker()


async def publish_session_updated(session_id: str, session_doc: dict | None = None) -> None:
    resolved_session = session_doc or await get_session_collection().find_one({"_id": session_id})
    if not resolved_session:
        return

    payload = SessionInDB.model_validate(resolved_session).model_dump(by_alias=True, mode="json")
    await session_stream_broker.publish(session_id, "session.updated", payload)


async def publish_message_upsert(message_doc: dict) -> None:
    payload = ChatMessage.model_validate(message_doc).model_dump(by_alias=True, mode="json")
    await session_stream_broker.publish(payload["session_id"], "message.upsert", payload)


async def publish_message_deleted(session_id: str, message_id: str) -> None:
    await session_stream_broker.publish(
        session_id,
        "message.deleted",
        {
            "session_id": session_id,
            "message_id": message_id,
        },
    )


async def publish_timeline_event(event_doc: dict) -> None:
    payload = SessionEvent.model_validate(event_doc).model_dump(by_alias=True, mode="json")
    await session_stream_broker.publish(payload["session_id"], "timeline.event", payload)


async def publish_context_refresh(session_id: str, reason: str, **payload: Any) -> None:
    await session_stream_broker.publish(
        session_id,
        "context.refresh",
        {
            "reason": reason,
            **jsonable_encoder(payload),
        },
    )