"""Session-scoped image attachment chunk uploads."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.config import settings

MAX_IMAGE_ATTACHMENT_BYTES = 64 * 1024 * 1024
MAX_IMAGE_ATTACHMENT_CHUNK_BYTES = 4 * 1024 * 1024
MAX_IMAGE_ATTACHMENT_CHUNKS = 64
INLINE_IMAGE_DIRECTORY = Path("_inline-images")
IMAGE_CHUNK_DIRECTORY = Path("_inline-image-chunks")
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
IMAGE_SUFFIXES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{6,96}$")


def _sanitize_file_name(file_name: str | None, mime_type: str, upload_id: str) -> str:
    raw_name = Path(file_name or "").name
    raw_stem = Path(raw_name).stem.strip() if raw_name else ""
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in raw_stem)
    safe_stem = safe_stem.strip(" .-") or "image"

    raw_suffix = Path(raw_name).suffix.lower() if raw_name else ""
    suffix = raw_suffix or IMAGE_SUFFIXES.get(mime_type.lower(), ".bin")
    if not suffix.startswith("."):
        suffix = f".{suffix}"

    return f"{safe_stem}-{upload_id[:10]}{suffix}"


def _session_upload_root(session_id: str) -> Path:
    return Path(settings.upload_dir) / session_id


def _relative_to_upload_root(path: Path) -> str:
    try:
        return path.relative_to(Path(settings.upload_dir)).as_posix()
    except ValueError:
        return path.name


def store_image_attachment_chunk(
    session_id: str,
    *,
    upload_id: str,
    chunk_index: int,
    total_chunks: int,
    total_size: int,
    payload: bytes,
    file_name: str | None,
    mime_type: str,
    width: int | None = None,
    height: int | None = None,
) -> dict:
    mime_type = mime_type.strip().lower()
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError("Unsupported image MIME type")
    if not _UPLOAD_ID_RE.match(upload_id):
        raise ValueError("Invalid image upload id")
    if total_size < 1 or total_size > MAX_IMAGE_ATTACHMENT_BYTES:
        raise ValueError("Image attachment exceeds the 64 MB upload limit")
    if total_chunks < 1 or total_chunks > MAX_IMAGE_ATTACHMENT_CHUNKS:
        raise ValueError("Invalid image chunk count")
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise ValueError("Invalid image chunk index")
    if len(payload) < 1 or len(payload) > MAX_IMAGE_ATTACHMENT_CHUNK_BYTES:
        raise ValueError("Invalid image chunk size")

    session_root = _session_upload_root(session_id)
    chunk_dir = session_root / IMAGE_CHUNK_DIRECTORY / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / f"{chunk_index:05d}.part").write_bytes(payload)

    chunk_paths = [chunk_dir / f"{index:05d}.part" for index in range(total_chunks)]
    if not all(path.exists() for path in chunk_paths):
        return {
            "complete": False,
            "received_chunks": sum(1 for path in chunk_paths if path.exists()),
            "total_chunks": total_chunks,
        }

    image_dir = session_root / INLINE_IMAGE_DIRECTORY
    image_dir.mkdir(parents=True, exist_ok=True)
    destination = image_dir / _sanitize_file_name(file_name, mime_type, upload_id)
    with destination.open("wb") as output:
        for chunk_path in chunk_paths:
            with chunk_path.open("rb") as chunk_file:
                shutil.copyfileobj(chunk_file, output)

    actual_size = destination.stat().st_size
    if actual_size != total_size:
        destination.unlink(missing_ok=True)
        raise ValueError("Image chunk upload size mismatch")

    shutil.rmtree(chunk_dir, ignore_errors=True)
    relative_path = _relative_to_upload_root(destination)
    return {
        "complete": True,
        "attachment": {
            "type": "image",
            "mime_type": mime_type,
            "display_name": Path(file_name or destination.name).name,
            "file_path": str(destination),
            "shared_relative_path": relative_path,
            "workspace_relative_path": relative_path,
            "width": width,
            "height": height,
            "size_bytes": actual_size,
        },
    }
