"""HLS / m3u8 streaming router."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.database import get_session_collection

router = APIRouter(prefix="/streams", tags=["streaming"])

HLS_DIR = Path(settings.hls_dir)
EXPORT_DIR = Path(settings.export_dir)


@router.get("/renders/{session_id}")
async def get_render_mp4(session_id: str):
    """Serve the latest rendered mp4 for a session."""
    session_doc = await get_session_collection().find_one({"_id": session_id})
    if not session_doc:
        raise HTTPException(404, "Session not found")

    render_path = session_doc.get("latest_render_path")
    if not render_path:
        raise HTTPException(404, "No rendered mp4 is available for this session")

    file_path = Path(render_path).resolve()
    export_root = EXPORT_DIR.resolve()

    try:
        file_path.relative_to(export_root)
    except ValueError as exc:
        raise HTTPException(400, "Invalid render path") from exc

    if not file_path.exists() or file_path.suffix.lower() != ".mp4":
        raise HTTPException(404, "Render file not found")

    return FileResponse(file_path, media_type="video/mp4", filename=file_path.name)


@router.get("/{stream_id}/{filename}")
async def get_hls_file(stream_id: str, filename: str):
    """Serve m3u8 playlist or .ts segments."""
    # Sanitize filename
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")

    file_path = HLS_DIR / stream_id / filename
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    if filename.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
    elif filename.endswith(".ts"):
        media_type = "video/MP2T"
    else:
        raise HTTPException(400, "Unsupported file type")

    return FileResponse(file_path, media_type=media_type)
