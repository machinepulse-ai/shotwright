"""Session-scoped reference video uploads and storyboard generation."""

from __future__ import annotations

import asyncio
import json
import math
import mimetypes
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.services.session_streams import publish_context_refresh

UPLOAD_DIR = Path(settings.upload_dir)
EXPORT_DIR = Path(settings.export_dir)
REFERENCE_VIDEOS_DIR = Path("_reference-videos")
REFERENCE_VIDEO_CHUNKS_DIR = Path("_reference-video-chunks")
STORYBOARDS_DIR = Path("_storyboards")
METADATA_SUFFIX = ".meta.json"
MAX_REFERENCE_VIDEO_BYTES = 500 * 1024 * 1024
MAX_REFERENCE_VIDEO_CHUNK_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_VIDEO_CHUNKS = 512
MIN_REFERENCE_VIDEO_DURATION_SECONDS = 1.0
MAX_REFERENCE_VIDEO_DURATION_SECONDS = 60.0
DEFAULT_STORYBOARD_INTERVAL_SECONDS = 1.0
DEFAULT_STORYBOARD_COLUMNS = 4
DEFAULT_STORYBOARD_WIDTH = 320
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{6,96}$")


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


def _session_export_dir(session_id: str) -> Path:
    return EXPORT_DIR / session_id


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


def _relative_to_session_storage(path: Path) -> str:
    resolved = path.resolve()
    for root in (UPLOAD_DIR.resolve(), EXPORT_DIR.resolve()):
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name


def _write_metadata(asset_path: Path, metadata: dict) -> None:
    _metadata_path(asset_path).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_metadata(metadata_path: Path) -> dict | None:
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _validate_reference_video_upload_request(upload_id: str, total_chunks: int, total_size: int) -> None:
    if not _UPLOAD_ID_RE.match(upload_id):
        raise ValueError("Invalid reference video upload id.")
    if total_size < 1 or total_size > MAX_REFERENCE_VIDEO_BYTES:
        raise ValueError("Reference video exceeds the 500 MB upload limit.")
    if total_chunks < 1 or total_chunks > MAX_REFERENCE_VIDEO_CHUNKS:
        raise ValueError("Invalid reference video chunk count.")


def _reference_video_chunk_root(session_id: str) -> Path:
    return _session_dir(session_id) / REFERENCE_VIDEO_CHUNKS_DIR


def _reference_video_chunk_dir(session_id: str, upload_id: str) -> Path:
    return _reference_video_chunk_root(session_id) / upload_id


def _reference_video_upload_manifest_path(session_id: str, upload_id: str) -> Path:
    return _reference_video_chunk_dir(session_id, upload_id) / "upload.json"


def _reference_video_upload_complete_marker_path(session_id: str, upload_id: str) -> Path:
    return _reference_video_chunk_root(session_id) / f"{upload_id}.complete.json"


def _reference_video_chunk_path(chunk_dir: Path, chunk_index: int) -> Path:
    return chunk_dir / f"{chunk_index:05d}.part"


def _load_completed_reference_video_upload(session_id: str, upload_id: str) -> dict | None:
    marker = _read_json(_reference_video_upload_complete_marker_path(session_id, upload_id))
    if not marker:
        return None
    metadata = marker.get("reference_video")
    if not isinstance(metadata, dict):
        return None
    file_path = Path(str(metadata.get("file_path") or ""))
    if not file_path.exists():
        return None
    return _resolve_video_metadata_from_path(session_id, file_path, metadata)


def _load_reference_video_upload_manifest(
    session_id: str,
    upload_id: str,
    *,
    total_chunks: int,
    total_size: int,
) -> dict:
    manifest_path = _reference_video_upload_manifest_path(session_id, upload_id)
    manifest = _read_json(manifest_path) or {}
    manifest_total_chunks = _parse_positive_int(manifest.get("total_chunks"))
    manifest_total_size = _parse_positive_int(manifest.get("total_size"))
    if manifest_total_chunks is not None and manifest_total_chunks != total_chunks:
        raise ValueError("Reference video upload chunk count changed; start a new upload.")
    if manifest_total_size is not None and manifest_total_size != total_size:
        raise ValueError("Reference video upload size changed; start a new upload.")
    return manifest


def _write_reference_video_upload_manifest(
    session_id: str,
    upload_id: str,
    *,
    total_chunks: int,
    total_size: int,
    filename: str | None,
    mime_type: str | None,
) -> None:
    manifest_path = _reference_video_upload_manifest_path(session_id, upload_id)
    existing = _load_reference_video_upload_manifest(
        session_id,
        upload_id,
        total_chunks=total_chunks,
        total_size=total_size,
    )
    payload = {
        **existing,
        "upload_id": upload_id,
        "total_chunks": total_chunks,
        "total_size": total_size,
        "filename": Path(filename or existing.get("filename") or "reference-video.mp4").name,
        "mime_type": (mime_type or existing.get("mime_type") or "video/mp4").strip() or "video/mp4",
        "updated_at": _utcnow().isoformat(),
    }
    payload.setdefault("created_at", payload["updated_at"])
    _write_json(manifest_path, payload)


def _received_reference_video_chunks(chunk_dir: Path, total_chunks: int) -> tuple[list[int], int]:
    received_chunks: list[int] = []
    received_bytes = 0
    for chunk_index in range(total_chunks):
        chunk_path = _reference_video_chunk_path(chunk_dir, chunk_index)
        if not chunk_path.exists():
            continue
        received_chunks.append(chunk_index)
        received_bytes += chunk_path.stat().st_size
    return received_chunks, received_bytes


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


def _parse_storyboard_crop_component(
    raw_value: object,
    *,
    field_name: str,
    frame_size: int,
    allow_zero: bool,
) -> int:
    text = str(raw_value).strip()
    if not text:
        raise ValueError(f"Storyboard crop {field_name} is required.")

    is_percent = text.endswith("%")
    numeric_text = text[:-1].strip() if is_percent else text
    try:
        numeric_value = float(numeric_text)
    except ValueError as exc:
        raise ValueError(
            f"Storyboard crop {field_name} must be a number in pixels or a percentage like 25%."
        ) from exc

    if not math.isfinite(numeric_value):
        raise ValueError(f"Storyboard crop {field_name} must be finite.")
    if allow_zero:
        if numeric_value < 0:
            raise ValueError(f"Storyboard crop {field_name} cannot be negative.")
    elif numeric_value <= 0:
        raise ValueError(f"Storyboard crop {field_name} must be greater than zero.")

    resolved_value = int(frame_size * numeric_value / 100.0) if is_percent else int(numeric_value)
    if allow_zero:
        if resolved_value < 0:
            raise ValueError(f"Storyboard crop {field_name} resolved outside the source frame.")
    elif resolved_value <= 0:
        raise ValueError(
            f"Storyboard crop {field_name} resolved to less than one pixel; increase the crop size."
        )
    return resolved_value


def _normalize_storyboard_crop(
    crop: object,
    *,
    source_width: int | None,
    source_height: int | None,
) -> dict[str, int] | None:
    if crop is None:
        return None
    if isinstance(crop, str) and not crop.strip():
        return None
    if source_width is None or source_height is None:
        raise ValueError("Storyboard crop requires the source video width and height metadata.")

    if isinstance(crop, str):
        raw_parts = [part.strip() for part in re.split(r"[:,]", crop) if part.strip()]
        if len(raw_parts) != 4:
            raise ValueError(
                "Storyboard crop must use x,y,width,height or x:y:width:height with pixels or percentages."
            )
        raw_x, raw_y, raw_width, raw_height = raw_parts
    elif isinstance(crop, dict):
        raw_x = crop.get("x", crop.get("left"))
        raw_y = crop.get("y", crop.get("top"))
        raw_width = crop.get("width", crop.get("w"))
        raw_height = crop.get("height", crop.get("h"))
        if any(value is None for value in (raw_x, raw_y, raw_width, raw_height)):
            raise ValueError("Storyboard crop objects must provide x, y, width, and height.")
    else:
        raise TypeError("Storyboard crop must be a string or an object with x, y, width, and height.")

    resolved_crop = {
        "x": _parse_storyboard_crop_component(raw_x, field_name="x", frame_size=source_width, allow_zero=True),
        "y": _parse_storyboard_crop_component(raw_y, field_name="y", frame_size=source_height, allow_zero=True),
        "width": _parse_storyboard_crop_component(
            raw_width,
            field_name="width",
            frame_size=source_width,
            allow_zero=False,
        ),
        "height": _parse_storyboard_crop_component(
            raw_height,
            field_name="height",
            frame_size=source_height,
            allow_zero=False,
        ),
    }

    if resolved_crop["x"] >= source_width or resolved_crop["y"] >= source_height:
        raise ValueError("Storyboard crop origin must stay inside the source frame.")
    if resolved_crop["x"] + resolved_crop["width"] > source_width:
        raise ValueError("Storyboard crop extends beyond the source frame width.")
    if resolved_crop["y"] + resolved_crop["height"] > source_height:
        raise ValueError("Storyboard crop extends beyond the source frame height.")
    return resolved_crop


def _build_storyboard_filter_graph(
    *,
    sampling_interval_seconds: float,
    tile_width: int,
    tile_columns: int,
    tile_rows: int,
    crop: dict[str, int] | None = None,
) -> str:
    filter_parts: list[str] = []
    if crop:
        filter_parts.append(f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}")
    filter_parts.extend(
        [
            f"fps=1/{sampling_interval_seconds}",
            f"scale={tile_width}:-1",
            f"tile={tile_columns}x{tile_rows}:margin=8:padding=8:color=white",
        ]
    )
    return ",".join(filter_parts)


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


def _thumbnail_path_for_video(file_path: Path) -> Path:
    return file_path.with_name(f"{file_path.stem}-cover.jpg")


def _generate_video_thumbnail(file_path: Path) -> Path | None:
    thumbnail_path = _thumbnail_path_for_video(file_path)
    if thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
        return thumbnail_path

    result = _run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "0.200",
            "-i",
            str(file_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=640:-2:force_original_aspect_ratio=decrease",
            "-q:v",
            "3",
            str(thumbnail_path),
        ],
        timeout=60,
    )
    if result.returncode != 0 or not thumbnail_path.exists() or thumbnail_path.stat().st_size <= 0:
        thumbnail_path.unlink(missing_ok=True)
        return None
    return thumbnail_path


def _ensure_video_thumbnail_metadata(resolved_path: Path, metadata: dict) -> bool:
    raw_thumbnail_path = str(metadata.get("thumbnail_path") or "").strip()
    thumbnail_path = Path(raw_thumbnail_path) if raw_thumbnail_path else None
    if thumbnail_path and thumbnail_path.is_file() and thumbnail_path.stat().st_size > 0:
        return False

    try:
        generated_thumbnail = _generate_video_thumbnail(resolved_path)
    except ReferenceMediaUnavailableError:
        return False
    if not generated_thumbnail:
        return False

    metadata.update(
        {
            "thumbnail_path": str(generated_thumbnail),
            "thumbnail_shared_relative_path": _relative_to_session_storage(generated_thumbnail),
            "thumbnail_mime_type": "image/jpeg",
            "thumbnail_size_bytes": generated_thumbnail.stat().st_size,
        }
    )
    return True


def _metadata_missing_video_probe_fields(metadata: dict) -> bool:
    return (
        _parse_positive_float(metadata.get("duration_seconds")) is None
        or _parse_positive_int(metadata.get("width")) is None
        or _parse_positive_int(metadata.get("height")) is None
    )


def _resolve_video_metadata_from_path(session_id: str, resolved_path: Path, metadata: dict | None = None) -> dict:
    resolved_metadata = dict(metadata or {})
    should_write_metadata = not metadata

    if _metadata_missing_video_probe_fields(resolved_metadata):
        probe = _probe_video(resolved_path)
        duration_seconds = _parse_positive_float(resolved_metadata.get("duration_seconds"))
        width = _parse_positive_int(resolved_metadata.get("width"))
        height = _parse_positive_int(resolved_metadata.get("height"))
        resolved_metadata.update(
            {
                "duration_seconds": duration_seconds if duration_seconds is not None else float(probe["duration_seconds"]),
                "width": width if width is not None else probe.get("width"),
                "height": height if height is not None else probe.get("height"),
            }
        )
        should_write_metadata = True

    base_fields = {
        "id": resolved_metadata.get("id") or uuid4().hex[:12],
        "session_id": resolved_metadata.get("session_id") or session_id,
        "filename": resolved_metadata.get("filename") or resolved_path.name,
        "file_path": str(resolved_path),
        "reference_video_path": str(resolved_path),
        "shared_relative_path": _relative_to_session_storage(resolved_path),
        "mime_type": resolved_metadata.get("mime_type") or mimetypes.guess_type(resolved_path.name)[0] or "video/mp4",
        "size_bytes": resolved_path.stat().st_size,
        "created_at": resolved_metadata.get("created_at") or _utcnow().isoformat(),
    }
    if any(resolved_metadata.get(key) != value for key, value in base_fields.items()):
        should_write_metadata = True
    resolved_metadata.update(base_fields)

    if _ensure_video_thumbnail_metadata(resolved_path, resolved_metadata):
        should_write_metadata = True

    if should_write_metadata:
        _write_metadata(resolved_path, resolved_metadata)

    return resolved_metadata


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
    entries: list[dict] = []
    for metadata in _load_asset_metadata(_asset_dir(session_id, REFERENCE_VIDEOS_DIR), limit=limit):
        file_path = Path(str(metadata.get("file_path") or ""))
        entries.append(_resolve_video_metadata_from_path(session_id, file_path, metadata) if file_path.exists() else metadata)
    return entries


def list_storyboards(session_id: str, *, limit: int | None = None) -> list[dict]:
    return _load_asset_metadata(_asset_dir(session_id, STORYBOARDS_DIR), limit=limit)


def _resolve_session_asset_path(session_id: str, raw_path: str, preferred_directory: Path) -> Path:
    session_root = _session_dir(session_id).resolve()
    export_root = _session_export_dir(session_id).resolve()
    preferred_root = _asset_dir(session_id, preferred_directory).resolve()
    requested_path = Path(raw_path)

    if requested_path.is_absolute():
        resolved = requested_path.resolve()
        try:
            resolved.relative_to(session_root)
        except ValueError:
            try:
                resolved.relative_to(export_root)
            except ValueError as exc:
                raise FileNotFoundError(
                    "Reference media paths must stay inside the session uploads or exports workspace."
                ) from exc
        if resolved.exists():
            return resolved
        raise FileNotFoundError(f"Reference media file not found at {resolved}")

    candidate_paths: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(candidate: Path) -> None:
        resolved_candidate = candidate.resolve()
        if resolved_candidate in seen:
            return
        seen.add(resolved_candidate)
        candidate_paths.append(resolved_candidate)

    add_candidate(session_root / requested_path)
    add_candidate(export_root / requested_path)

    if requested_path.parts and requested_path.parts[0] == session_id:
        add_candidate(UPLOAD_DIR.resolve() / requested_path)
        add_candidate(EXPORT_DIR.resolve() / requested_path)

    add_candidate(preferred_root / requested_path.name)
    add_candidate(export_root / requested_path.name)

    for candidate_path in candidate_paths:
        if candidate_path.exists():
            return candidate_path

    raise FileNotFoundError(f"Reference media file not found for {raw_path}")


def _finalize_reference_video_file(session_id: str, file_path: Path, *, mime_type: str | None = None) -> dict:
    metadata_path = _metadata_path(file_path)
    thumbnail_path = _thumbnail_path_for_video(file_path)
    size_bytes = file_path.stat().st_size
    if size_bytes < 1:
        raise ValueError("Reference video is empty.")
    if size_bytes > MAX_REFERENCE_VIDEO_BYTES:
        raise ValueError("Reference video exceeds the 500 MB upload limit.")

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
            "shared_relative_path": _relative_to_session_storage(file_path),
            "mime_type": (mime_type or mimetypes.guess_type(file_path.name)[0] or "video/mp4").strip() or "video/mp4",
            "size_bytes": size_bytes,
            "duration_seconds": duration_seconds,
            "width": video_probe.get("width"),
            "height": video_probe.get("height"),
            "created_at": created_at,
        }
        _ensure_video_thumbnail_metadata(file_path, metadata)
        _write_metadata(file_path, metadata)
    except Exception:
        file_path.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise

    _publish_context_refresh_in_background(
        session_id,
        "reference_video.uploaded",
        reference_video_path=metadata["shared_relative_path"],
    )

    return metadata


def upload_reference_video(session_id: str, file_bytes: bytes, filename: str) -> dict:
    _ensure_upload_dir()
    if len(file_bytes) > MAX_REFERENCE_VIDEO_BYTES:
        raise ValueError("Reference video exceeds the 500 MB upload limit.")

    reference_video_dir = _ensure_asset_dir(session_id, REFERENCE_VIDEOS_DIR)
    safe_name = _sanitize_file_name(filename, "reference-video", ".mp4")
    file_path = _build_unique_path(reference_video_dir, safe_name)
    file_path.write_bytes(file_bytes)
    return _finalize_reference_video_file(session_id, file_path)


def get_reference_video_upload_status(
    session_id: str,
    *,
    upload_id: str,
    total_chunks: int,
    total_size: int,
) -> dict:
    _validate_reference_video_upload_request(upload_id, total_chunks, total_size)

    completed_metadata = _load_completed_reference_video_upload(session_id, upload_id)
    if completed_metadata:
        return {
            "complete": True,
            "upload_id": upload_id,
            "received_chunks": list(range(total_chunks)),
            "received_chunk_count": total_chunks,
            "received_bytes": int(completed_metadata.get("size_bytes") or total_size),
            "total_chunks": total_chunks,
            "total_size": total_size,
            "reference_video": completed_metadata,
        }

    chunk_dir = _reference_video_chunk_dir(session_id, upload_id)
    _load_reference_video_upload_manifest(
        session_id,
        upload_id,
        total_chunks=total_chunks,
        total_size=total_size,
    )
    received_chunks, received_bytes = _received_reference_video_chunks(chunk_dir, total_chunks)
    return {
        "complete": False,
        "upload_id": upload_id,
        "received_chunks": received_chunks,
        "received_chunk_count": len(received_chunks),
        "received_bytes": received_bytes,
        "total_chunks": total_chunks,
        "total_size": total_size,
    }


def store_reference_video_chunk(
    session_id: str,
    *,
    upload_id: str,
    chunk_index: int,
    total_chunks: int,
    total_size: int,
    payload: bytes,
    filename: str | None,
    mime_type: str | None,
) -> dict:
    _validate_reference_video_upload_request(upload_id, total_chunks, total_size)
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise ValueError("Invalid reference video chunk index.")
    if len(payload) < 1 or len(payload) > MAX_REFERENCE_VIDEO_CHUNK_BYTES:
        raise ValueError("Invalid reference video chunk size.")

    chunk_dir = _reference_video_chunk_dir(session_id, upload_id)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    _write_reference_video_upload_manifest(
        session_id,
        upload_id,
        total_chunks=total_chunks,
        total_size=total_size,
        filename=filename,
        mime_type=mime_type,
    )
    _reference_video_chunk_path(chunk_dir, chunk_index).write_bytes(payload)
    return get_reference_video_upload_status(
        session_id,
        upload_id=upload_id,
        total_chunks=total_chunks,
        total_size=total_size,
    )


def complete_reference_video_chunk_upload(
    session_id: str,
    *,
    upload_id: str,
    total_chunks: int,
    total_size: int,
    filename: str | None,
    mime_type: str | None,
) -> dict:
    _validate_reference_video_upload_request(upload_id, total_chunks, total_size)

    completed_metadata = _load_completed_reference_video_upload(session_id, upload_id)
    if completed_metadata:
        return completed_metadata

    manifest = _load_reference_video_upload_manifest(
        session_id,
        upload_id,
        total_chunks=total_chunks,
        total_size=total_size,
    )
    resolved_filename = Path(filename or manifest.get("filename") or "reference-video.mp4").name
    resolved_mime_type = (mime_type or manifest.get("mime_type") or "video/mp4").strip() or "video/mp4"

    chunk_dir = _reference_video_chunk_dir(session_id, upload_id)
    chunk_paths = [_reference_video_chunk_path(chunk_dir, index) for index in range(total_chunks)]
    missing_chunks = [index for index, path in enumerate(chunk_paths) if not path.exists()]
    if missing_chunks:
        preview = ", ".join(str(index) for index in missing_chunks[:8])
        suffix = "..." if len(missing_chunks) > 8 else ""
        raise ValueError(f"Reference video upload is missing chunks: {preview}{suffix}")

    reference_video_dir = _ensure_asset_dir(session_id, REFERENCE_VIDEOS_DIR)
    safe_name = _sanitize_file_name(resolved_filename, "reference-video", ".mp4")
    file_path = _build_unique_path(reference_video_dir, safe_name)

    try:
        with file_path.open("wb") as output:
            for chunk_path in chunk_paths:
                with chunk_path.open("rb") as chunk_file:
                    shutil.copyfileobj(chunk_file, output)

        actual_size = file_path.stat().st_size
        if actual_size != total_size:
            file_path.unlink(missing_ok=True)
            raise ValueError("Reference video chunk upload size mismatch.")

        metadata = _finalize_reference_video_file(session_id, file_path, mime_type=resolved_mime_type)
        _write_json(
            _reference_video_upload_complete_marker_path(session_id, upload_id),
            {
                "upload_id": upload_id,
                "total_chunks": total_chunks,
                "total_size": total_size,
                "reference_video": metadata,
                "completed_at": _utcnow().isoformat(),
            },
        )
        shutil.rmtree(chunk_dir, ignore_errors=True)
        return metadata
    except Exception:
        file_path.unlink(missing_ok=True)
        raise


def _resolve_reference_video_metadata(session_id: str, reference_video_path: str | None = None) -> dict:
    if reference_video_path:
        resolved_path = _resolve_session_asset_path(session_id, reference_video_path, REFERENCE_VIDEOS_DIR)
        metadata = _read_metadata(_metadata_path(resolved_path))
        return _resolve_video_metadata_from_path(session_id, resolved_path, metadata)

    candidates = list_reference_videos(session_id, limit=1)
    if not candidates:
        raise ValueError("No uploaded reference videos are available for this session.")
    candidate_path = Path(str(candidates[0].get("file_path") or ""))
    if candidate_path.exists():
        return _resolve_video_metadata_from_path(session_id, candidate_path, candidates[0])
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
    crop: str | dict[str, object] | None = None,
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
    source_video_width = _parse_positive_int(source_video.get("width"))
    source_video_height = _parse_positive_int(source_video.get("height"))
    normalized_crop = _normalize_storyboard_crop(
        crop,
        source_width=source_video_width,
        source_height=source_video_height,
    )
    filter_graph = _build_storyboard_filter_graph(
        sampling_interval_seconds=sampling_interval_seconds,
        tile_width=tile_width,
        tile_columns=tile_columns,
        tile_rows=tile_rows,
        crop=normalized_crop,
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
        "shared_relative_path": _relative_to_session_storage(output_path),
        "mime_type": "image/jpeg",
        "created_at": created_at,
        "source_video_path": str(source_video_path),
        "source_video_relative_path": source_video["shared_relative_path"],
        "source_video_filename": source_video["filename"],
        "source_video_duration_seconds": total_duration_seconds,
        "source_video_width": source_video_width,
        "source_video_height": source_video_height,
        "clip_start_seconds": round(clip_start_seconds, 3),
        "clip_end_seconds": round(clip_end_seconds, 3),
        "clip_duration_seconds": effective_clip_duration,
        "interval_seconds": sampling_interval_seconds,
        "columns": tile_columns,
        "rows": tile_rows,
        "tile_width": tile_width,
        "estimated_frames": estimated_frames,
        "crop": normalized_crop,
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
