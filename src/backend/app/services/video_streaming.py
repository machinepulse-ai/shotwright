"""HLS / m3u8 video streaming service.

Converts rendered mp4 to HLS segments for browser-friendly streaming.
"""

import logging
import os
import subprocess
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

HLS_DIR = Path(settings.hls_dir)


def _ensure_hls_dir() -> None:
    HLS_DIR.mkdir(parents=True, exist_ok=True)


async def generate_hls(mp4_path: str, stream_id: str) -> dict:
    """Convert an mp4 to HLS m3u8 + ts segments."""
    _ensure_hls_dir()
    out_dir = HLS_DIR / stream_id
    out_dir.mkdir(parents=True, exist_ok=True)

    playlist = out_dir / "index.m3u8"

    cmd = [
        "ffmpeg",
        "-i", mp4_path,
        "-codec", "copy",
        "-start_number", "0",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-f", "hls",
        str(playlist),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error("ffmpeg HLS conversion failed: %s", result.stderr)
            return {"success": False, "error": result.stderr[-500:]}
    except FileNotFoundError:
        return {"success": False, "error": "ffmpeg not found on host"}

    segments = [f for f in os.listdir(out_dir) if f.endswith(".ts")]

    return {
        "success": True,
        "stream_id": stream_id,
        "playlist_url": f"/api/streams/{stream_id}/index.m3u8",
        "segments": len(segments),
    }
