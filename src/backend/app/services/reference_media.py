"""Session-scoped reference video uploads and storyboard generation."""

from __future__ import annotations

import asyncio
import json
import math
import mimetypes
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.services.session_streams import publish_context_refresh

UPLOAD_DIR = Path(settings.upload_dir)
REFERENCE_VIDEOS_DIR = Path("_reference-videos")
STORYBOARDS_DIR = Path("_storyboards")
METADATA_SUFFIX = ".meta.json"
MAX_REFERENCE_VIDEO_BYTES = 500 * 1024 * 1024
MIN_REFERENCE_VIDEO_DURATION_SECONDS = 1.0
MAX_REFERENCE_VIDEO_DURATION_SECONDS = 60.0
DEFAULT_STORYBOARD_INTERVAL_SECONDS = 1.0
DEFAULT_STORYBOARD_COLUMNS = 4
DEFAULT_STORYBOARD_WIDTH = 320


class ReferenceMediaUnavailableError(RuntimeError):
    """Raised when ffmpeg/ffprobe is unavailable in the backend runtime."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_upload_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_file_name(value: str | None, fallback_stem: str, fallback_suffix: str) -> str:
    raw_name = Path(value).name if value else ""
    raw_stem = Path(raw_name).stem.strip() if raw_name else fallback_stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', raw_stem).strip().strip('.') or fallback_stem
    safe_suffix = Path(raw_name).suffix.lower() if raw_name else fallback_suffix.lower()
    if not safe_suffix.startswith('.'):
        safe_suffix = f'.{safe_suffix}'
    return f"{safe_stem}{safe_suffix or fallback_suffix}"


def _session_dir(session_id: str) -> Path:
    return UPLOAD_DIR / session_id


def _asset_dir(session_id: str, asset_directory: Path) -> Path:
    return _session_dir(session_id) / asset_directory


def _ensure_asset_dir(session_id: str, asset_directory: Path) -> Path:
    directory = _asset_dir(session_id, asset_directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _metadata_path(asset_path: Path) -> Path:
    return asset_path.parent / f"{asset_path.name}{METADATA_SUFFIX}"


def _build_unique_path(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate

    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem}-{index:02d}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{uuid4().hex[:8]}{suffix}"


def _relative_to_uploads(path: Path) -> str:
    try:
        return path.resolve().relative_to(UPLOAD_DIR.resolve()).as_posix()
    except ValueError:
        return path.name


def _write_metadata(asset_path: Path, metadata: dict) -> None:
    _metadata_path(asset_path).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_metadata(metadata_path: Path) -> dict | None:
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_positive_float(value: object) -> float | None:
    try:
        resolved = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(resolved) or resolved <= 0:
        return None
    return resolved


def _parse_positive_int(value: object) -> int | None:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return None
    if resolved <= 0:
        return None
    return resolved


def _run_command(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ReferenceMediaUnavailableError(f"{command[0]} is not available in the Shotwright backend runtime.") from exc


def _publish_context_refresh_in_background(session_id: str, reason: str, **payload: object) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(publish_context_refresh(session_id, reason, **payload))


def _probe_video(file_path: Path) -> dict:
    result = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ValueError(stderr or "Uploaded file is not a readable video for ffmpeg/ffprobe.")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid metadata for the uploaded reference video.") from exc

    streams = payload.get("streams") or []
    video_stream = next((stream for stream in streams if str(stream.get("codec_type") or "").lower() == "video"), None)
    if not isinstance(video_stream, dict):
        raise ValueError("Uploaded file does not contain a video stream that ffmpeg can sample.")

    duration_candidates = [
        _parse_positive_float((payload.get("format") or {}).get("duration")),
        _parse_positive_float(video_stream.get("duration")),
    ]
    duration_seconds = next((value for value in duration_candidates if value is not None), None)
    if duration_seconds is None:
        raise ValueError("Could not determine the uploaded reference video duration.")

    width = _parse_positive_int(video_stream.get("width"))
    height = _parse_positive_int(video_stream.get("height"))
    return {
        "duration_seconds": round(duration_seconds, 3),
        "width": width,
        "height": height,
    }


def _load_asset_metadata(directory: Path, *, limit: int | None = None) -> list[dict]:
    if not directory.exists():
        return []

    entries: list[dict] = []
    for metadata_path in sorted(directory.glob(f"*{METADATA_SUFFIX}"), key=lambda path: path.stat().st_mtime, reverse=True):
        metadata = _read_metadata(metadata_path)
        if not metadata:
            continue
        file_path = Path(str(metadata.get("file_path") or ""))
        if not file_path.exists():
            continue
        entries.append(metadata)
        if limit is not None and len(entries) >= limit:
            break
    return entries


def list_reference_videos(session_id: str, *, limit: int | None = None) -> list[dict]:
    return _load_asset_metadata(_asset_dir(session_id, REFERENCE_VIDEOS_DIR), limit=limit)


def list_storyboards(session_id: str, *, limit: int | None = None) -> list[dict]:
    return _load_asset_metadata(_asset_dir(session_id, STORYBOARDS_DIR), limit=limit)


def _resolve_session_asset_path(session_id: str, raw_path: str, preferred_directory: Path) -> Path:
    session_root = _session_dir(session_id).resolve()
    preferred_root = _asset_dir(session_id, preferred_directory).resolve()
    requested_path = Path(raw_path)

    if requested_path.is_absolute():
        resolved = requested_path.resolve()
        try:
            resolved.relative_to(session_root)
        except ValueError as exc:
            raise FileNotFoundError("Reference media paths must stay inside the session temporary workspace.") from exc
        if resolved.exists():
            return resolved
        raise FileNotFoundError(f"Reference media file not found at {resolved}")

    direct_candidate = (session_root / requested_path).resolve()
    if direct_candidate.exists():
        return direct_candidate

    preferred_candidate = (preferred_root / requested_path.name).resolve()
    if preferred_candidate.exists():
        return preferred_candidate

    raise FileNotFoundError(f"Reference media file not found for {raw_path}")


def upload_reference_video(session_id: str, file_bytes: bytes, filename: str) -> dict:
    _ensure_upload_dir()
    if len(file_bytes) > MAX_REFERENCE_VIDEO_BYTES:
        raise ValueError("Reference video exceeds the 500 MB upload limit.")

    reference_video_dir = _ensure_asset_dir(session_id, REFERENCE_VIDEOS_DIR)
    safe_name = _sanitize_file_name(filename, "reference-video", ".mp4")
    file_path = _build_unique_path(reference_video_dir, safe_name)
    metadata_path = _metadata_path(file_path)
    file_path.write_bytes(file_bytes)

    try:
        video_probe = _probe_video(file_path)
        duration_seconds = float(video_probe["duration_seconds"])
        if duration_seconds < MIN_REFERENCE_VIDEO_DURATION_SECONDS or duration_seconds > MAX_REFERENCE_VIDEO_DURATION_SECONDS:
            raise ValueError("Reference video duration must stay between 1 and 60 seconds.")

        created_at = _utcnow().isoformat()
        metadata = {
            "id": uuid4().hex[:12],
            "session_id": session_id,
            "filename": file_path.name,
            "file_path": str(file_path),
            "reference_video_path": str(file_path),
            "shared_relative_path": _relative_to_uploads(file_path),
            "mime_type": mimetypes.guess_type(file_path.name)[0] or "video/mp4",
            "size_bytes": len(file_bytes),
            "duration_seconds": duration_seconds,
            "width": video_probe.get("width"),
            "height": video_probe.get("height"),
            "created_at": created_at,
        }
        _write_metadata(file_path, metadata)
    except Exception:
        file_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise

    _publish_context_refresh_in_background(
        session_id,
        "reference_video.uploaded",
        reference_video_path=metadata["shared_relative_path"],
    )

    return metadata


def _resolve_reference_video_metadata(session_id: str, reference_video_path: str | None = None) -> dict:
    if reference_video_path:
        resolved_path = _resolve_session_asset_path(session_id, reference_video_path, REFERENCE_VIDEOS_DIR)
        metadata = _read_metadata(_metadata_path(resolved_path))
        if metadata:
            return metadata

        probe = _probe_video(resolved_path)
        return {
            "id": uuid4().hex[:12],
            "session_id": session_id,
            "filename": resolved_path.name,
            "file_path": str(resolved_path),
            "reference_video_path": str(resolved_path),
            "shared_relative_path": _relative_to_uploads(resolved_path),
            "mime_type": mimetypes.guess_type(resolved_path.name)[0] or "video/mp4",
            "size_bytes": resolved_path.stat().st_size,
            "duration_seconds": float(probe["duration_seconds"]),
            "width": probe.get("width"),
            "height": probe.get("height"),
            "created_at": _utcnow().isoformat(),
        }

    candidates = list_reference_videos(session_id, limit=1)
    if not candidates:
        raise ValueError("No uploaded reference videos are available for this session.")
    return candidates[0]


def generate_storyboard(
    session_id: str,
    *,
    reference_video_path: str | None = None,
    output_name: str | None = None,
    start_seconds: float | None = None,
    clip_duration_seconds: float | None = None,
    interval_seconds: float | None = None,
    columns: int | None = None,
    width: int | None = None,
) -> dict:
    _ensure_upload_dir()
    source_video = _resolve_reference_video_metadata(session_id, reference_video_path)
    source_video_path = Path(str(source_video["file_path"]))
    if not source_video_path.exists():
        raise FileNotFoundError(f"Reference video not found at {source_video_path}")

    total_duration_seconds = max(MIN_REFERENCE_VIDEO_DURATION_SECONDS, float(source_video["duration_seconds"]))
    clip_start_seconds = max(0.0, float(start_seconds or 0.0))
    if clip_start_seconds >= total_duration_seconds:
        raise ValueError("Storyboard start_seconds must be earlier than the reference video duration.")

    requested_clip_duration = float(clip_duration_seconds or (total_duration_seconds - clip_start_seconds))
    if requested_clip_duration <= 0:
        raise ValueError("Storyboard clip duration must be greater than zero.")

    clip_end_seconds = min(total_duration_seconds, clip_start_seconds + requested_clip_duration)
    effective_clip_duration = round(max(0.1, clip_end_seconds - clip_start_seconds), 3)
    sampling_interval_seconds = max(0.1, float(interval_seconds or DEFAULT_STORYBOARD_INTERVAL_SECONDS))
    tile_columns = max(1, int(columns or DEFAULT_STORYBOARD_COLUMNS))
    tile_width = max(64, int(width or DEFAULT_STORYBOARD_WIDTH))
    estimated_frames = max(1, int(math.ceil(effective_clip_duration / sampling_interval_seconds)))
    tile_rows = max(1, int(math.ceil(estimated_frames / tile_columns)))
    filter_graph = (
        f"fps=1/{sampling_interval_seconds},"
        f"scale={tile_width}:-1,"
        f"tile={tile_columns}x{tile_rows}:margin=8:padding=8:color=white"
    )

    storyboard_dir = _ensure_asset_dir(session_id, STORYBOARDS_DIR)
    safe_output_name = _sanitize_file_name(output_name, f"{source_video_path.stem}-storyboard", ".jpg")
    output_path = _build_unique_path(storyboard_dir, safe_output_name)

    command = ["ffmpeg", "-y", "-ss", f"{clip_start_seconds:.3f}", "-i", str(source_video_path)]
    if effective_clip_duration < total_duration_seconds:
        command.extend(["-t", f"{effective_clip_duration:.3f}"])
    command.extend(["-vf", filter_graph, "-frames:v", "1", str(output_path)])

    result = _run_command(command, timeout=120)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ValueError(stderr or "ffmpeg failed while generating the storyboard contact sheet.")
    if not output_path.exists():
        raise ValueError("ffmpeg finished without producing a storyboard image.")

    created_at = _utcnow().isoformat()
    metadata = {
        "id": uuid4().hex[:12],
        "session_id": session_id,
        "filename": output_path.name,
        "file_path": str(output_path),
        "storyboard_image_path": str(output_path),
        "shared_relative_path": _relative_to_uploads(output_path),
        "mime_type": "image/jpeg",
        "created_at": created_at,
        "source_video_path": str(source_video_path),
        "source_video_relative_path": source_video["shared_relative_path"],
        "source_video_filename": source_video["filename"],
        "source_video_duration_seconds": total_duration_seconds,
        "clip_start_seconds": round(clip_start_seconds, 3),
        "clip_end_seconds": round(clip_end_seconds, 3),
        "clip_duration_seconds": effective_clip_duration,
        "interval_seconds": sampling_interval_seconds,
        "columns": tile_columns,
        "rows": tile_rows,
        "tile_width": tile_width,
        "estimated_frames": estimated_frames,
        "ffmpeg_filter": filter_graph,
    }
    _write_metadata(output_path, metadata)

    _publish_context_refresh_in_background(
        session_id,
        "storyboard.generated",
        storyboard_path=metadata["shared_relative_path"],
        source_video_path=metadata["source_video_relative_path"],
    )

    return metadata