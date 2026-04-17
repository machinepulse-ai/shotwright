"""HLS / m3u8 streaming router."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(prefix="/streams", tags=["streaming"])

HLS_DIR = Path(settings.hls_dir)


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
