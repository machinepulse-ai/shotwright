"""AEP project management — upload, extract, export, and render."""

from datetime import datetime, timezone
import os
import re
import zipfile
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from app.config import settings
from app.database import get_project_collection, get_session_collection
from app.services.session_streams import publish_context_refresh, publish_session_updated

UPLOAD_DIR = Path(settings.upload_dir)
EXPORT_DIR = Path(settings.export_dir)


def _ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _list_relative_files(base_dir: Path) -> list[str]:
    return [str(path.relative_to(base_dir)) for path in base_dir.rglob("*") if path.is_file()]


def _sanitize_windows_filename(value: str, fallback: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', value).strip().strip('.')
    return sanitized or fallback


def _normalize_aep_filename(filename: str | None, fallback_stem: str) -> str:
    candidate = _sanitize_windows_filename(filename or fallback_stem, fallback_stem)
    if candidate.lower().endswith('.aep'):
        return candidate
    return f"{candidate}.aep"


async def upload_project(session_id: str, file_bytes: bytes, filename: str) -> dict:
    """Save uploaded zip into the shared uploads volume and extract it for agent access."""
    _ensure_dirs()
    project_id = str(uuid4())
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    zip_path = session_dir / f"{project_id}.zip"
    zip_path.write_bytes(file_bytes)

    extract_dir = session_dir / project_id
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            member_path = PureWindowsPath(member)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"Unsafe path in zip: {member}")
        zf.extractall(extract_dir)

    relative_files = _list_relative_files(extract_dir)
    aep_files = [path for path in relative_files if path.lower().endswith(".aep")]
    project_doc = {
        "_id": project_id,
        "session_id": session_id,
        "filename": filename,
        "workspace_dir": str(extract_dir),
        "aep_files": aep_files,
        "entry_aep_file": aep_files[0] if aep_files else None,
        "origin": "uploaded",
        "created_at": datetime.now(timezone.utc),
        "status": "uploaded",
    }
    await get_project_collection().insert_one(project_doc)

    session_col = get_session_collection()
    session_doc = await session_col.find_one({"_id": session_id})
    if session_doc and not session_doc.get("active_project_id"):
        await session_col.update_one(
            {"_id": session_id},
            {"$set": {"active_project_id": project_id, "updated_at": project_doc["created_at"]}},
        )
        project_doc["status"] = "active"
        await get_project_collection().update_one({"_id": project_id}, {"$set": {"status": "active"}})
        await publish_session_updated(session_id)

    await publish_context_refresh(session_id, "project.uploaded", project_id=project_id)

    return project_doc


async def create_project_workspace(
    session_id: str,
    project_name: str | None = None,
    aep_filename: str | None = None,
    *,
    set_active: bool = False,
) -> dict:
    """Create a managed workspace for a generated After Effects project."""
    _ensure_dirs()
    project_id = str(uuid4())
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    workspace_dir = session_dir / project_id
    workspace_dir.mkdir(parents=True, exist_ok=True)

    fallback_stem = f"project-{project_id[:8]}"
    entry_stem = _sanitize_windows_filename(project_name, fallback_stem) if project_name else fallback_stem
    entry_aep_file = _normalize_aep_filename(aep_filename, entry_stem)

    project_doc = {
        "_id": project_id,
        "session_id": session_id,
        "filename": entry_aep_file,
        "workspace_dir": str(workspace_dir),
        "aep_files": [],
        "entry_aep_file": entry_aep_file,
        "origin": "generated",
        "created_at": datetime.now(timezone.utc),
        "status": "uploaded",
    }
    await get_project_collection().insert_one(project_doc)

    if set_active:
        await set_active_project(session_id, project_id)
        project_doc["status"] = "active"
    else:
        await publish_context_refresh(session_id, "project.created", project_id=project_id)

    refreshed = await get_project(session_id, project_id)
    return refreshed or project_doc


async def refresh_project_files(session_id: str, project_id: str) -> dict | None:
    """Rescan a managed project workspace after JSX changes."""
    project = await get_project(session_id, project_id)
    if not project:
        return None

    source_dir = Path(project["workspace_dir"])
    if not source_dir.exists():
        return None

    relative_files = _list_relative_files(source_dir)
    aep_files = sorted(path for path in relative_files if path.lower().endswith('.aep'))
    entry_aep_file = project.get("entry_aep_file")
    if not entry_aep_file and aep_files:
        entry_aep_file = aep_files[0]
    elif entry_aep_file and aep_files and entry_aep_file not in aep_files:
        entry_aep_file = aep_files[0]

    await get_project_collection().update_one(
        {"_id": project_id, "session_id": session_id},
        {"$set": {"aep_files": aep_files, "entry_aep_file": entry_aep_file}},
    )

    refreshed = await get_project(session_id, project_id)
    await publish_context_refresh(session_id, "project.updated", project_id=project_id)
    return refreshed


async def list_projects(session_id: str) -> list[dict]:
    return await get_project_collection().find({"session_id": session_id}).sort("created_at", -1).to_list(length=100)


async def get_project(session_id: str, project_id: str) -> dict | None:
    return await get_project_collection().find_one({"_id": project_id, "session_id": session_id})


async def set_active_project(session_id: str, project_id: str) -> None:
    await get_project_collection().update_many(
        {"session_id": session_id},
        {"$set": {"status": "uploaded"}},
    )
    await get_project_collection().update_one(
        {"_id": project_id, "session_id": session_id},
        {"$set": {"status": "active"}},
    )
    await get_session_collection().update_one(
        {"_id": session_id},
        {"$set": {"active_project_id": project_id, "updated_at": datetime.now(timezone.utc)}},
    )
    await publish_session_updated(session_id)
    await publish_context_refresh(session_id, "project.selected", project_id=project_id)


async def export_project(session_id: str, project_id: str) -> Path | None:
    """Pack the shared project workspace into a downloadable zip."""
    _ensure_dirs()
    project = await get_project(session_id, project_id)
    if not project:
        return None

    export_dir = EXPORT_DIR / session_id
    export_dir.mkdir(parents=True, exist_ok=True)

    export_zip = export_dir / f"{project_id}-export.zip"
    source_dir = Path(project["workspace_dir"])

    if not source_dir.exists():
        return None

    with zipfile.ZipFile(export_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(source_dir):
            for f in files:
                full = Path(root) / f
                arcname = full.relative_to(source_dir)
                zf.write(full, arcname)

    await get_project_collection().update_one(
        {"_id": project_id},
        {"$set": {"status": "exported"}},
    )
    await publish_context_refresh(session_id, "project.exported", project_id=project_id)
    return export_zip
