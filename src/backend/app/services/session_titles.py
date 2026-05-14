"""Helpers for deriving concise session titles from the first useful user turn."""

from __future__ import annotations

import re
import logging
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.database import get_session_collection
from app.services.session_streams import publish_session_updated

_DEFAULT_TITLE_PATTERNS = (
    re.compile(r"^untitled session$", re.IGNORECASE),
    re.compile(r"^session\s+\d+$", re.IGNORECASE),
    re.compile(r"^会话\s*\d+$"),
    re.compile(r"^新会话\s*\d*$"),
)
_LEADING_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s*|\d+[.)、]\s*)+")
_URL_RE = re.compile(r"https?://\S+")
_SPACE_RE = re.compile(r"\s+")
_LOW_VALUE_PROMPTS = {
    "hi",
    "hello",
    "hey",
    "你好",
    "您好",
    "在吗",
    "哈喽",
    "继续",
}
logger = logging.getLogger(__name__)


def _looks_like_default_session_name(value: object) -> bool:
    name = str(value or "").strip()
    if not name:
        return True
    return any(pattern.match(name) for pattern in _DEFAULT_TITLE_PATTERNS)


def _trim_title(value: str, *, max_chars: int = 32) -> str:
    title = value.strip(" \t\r\n\"'`*_#：:，,。.!！?？-")
    if len(title) <= max_chars:
        return title
    return f"{title[:max_chars].rstrip()}..."


def _is_low_value_prompt(value: str) -> bool:
    normalized = re.sub(r"[\s,，.。!！?？~～:：;；]+", "", value).lower()
    return normalized in _LOW_VALUE_PROMPTS


def derive_session_title(content: str, attachments: list[dict] | None = None) -> str | None:
    """Return a compact, deterministic title without calling an LLM."""

    lines = []
    for raw_line in content.splitlines():
        line = _URL_RE.sub("", raw_line).strip()
        line = _LEADING_LIST_MARKER_RE.sub("", line).strip()
        if not line:
            continue
        if line.startswith("```") or line.startswith("#"):
            continue
        if _is_low_value_prompt(line):
            continue
        lines.append(line)
        if lines:
            break

    candidate = _SPACE_RE.sub(" ", " ".join(lines)).strip()
    if not candidate and attachments:
        image_count = sum(1 for item in attachments if isinstance(item, dict) and item.get("type") == "image")
        video_count = sum(1 for item in attachments if isinstance(item, dict) and item.get("type") == "reference_video")
        if video_count and image_count:
            candidate = "参考图片与视频制作"
        elif video_count:
            candidate = "参考视频制作"
        elif image_count:
            candidate = "参考图片制作"

    title = _trim_title(candidate)
    return title or None


async def maybe_auto_title_session(
    session_id: str,
    content: str,
    attachments: list[dict] | None = None,
) -> str | None:
    """Update the session name once, only while it still has the default generated name."""

    title = derive_session_title(content, attachments)
    if not title:
        return None

    try:
        session_collection = get_session_collection()
        session_doc = await session_collection.find_one({"_id": session_id})
        if not session_doc or not _looks_like_default_session_name(session_doc.get("name")):
            return None

        updated = await session_collection.find_one_and_update(
            {
                "_id": session_id,
                "name": session_doc.get("name"),
            },
            {
                "$set": {
                    "name": title,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            return None

        await publish_session_updated(session_id, updated)
        return title
    except Exception as exc:  # noqa: BLE001 - title generation must never break a chat turn.
        logger.debug("Skipping automatic session title for %s: %s", session_id, exc)
        return None
