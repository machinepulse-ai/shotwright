"""AEP project management — upload, extract, export, and render."""

import logging
import os
import shutil
import zipfile
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from app.config import settings
from app.services.container_manager import exec_in_container, get_container

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(settings.upload_dir)
EXPORT_DIR = Path(settings.export_dir)


def _ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


async def upload_project(session_id: str, container_db_id: str, file_bytes: bytes, filename: str) -> dict:
    """Save uploaded zip and extract into the container's data volume."""
    _ensure_dirs()
    project_id = str(uuid4())
    zip_path = UPLOAD_DIR / f"{project_id}.zip"
    zip_path.write_bytes(file_bytes)

    extract_dir = UPLOAD_DIR / project_id
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Validate paths to prevent zip-slip
        for member in zf.namelist():
            member_path = PureWindowsPath(member)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"Unsafe path in zip: {member}")
        zf.extractall(extract_dir)

    container = await get_container(container_db_id)
    if not container:
        raise ValueError("Container not found")

    # Copy extracted files into the container
    container_target = f"C:\\data\\projects\\{project_id}"
    exit_code, output = await exec_in_container(
        container["docker_id"],
        ["powershell", "-Command", f"New-Item -ItemType Directory -Force -Path '{container_target}'"],
    )
    logger.info("Created project dir in container: exit=%d output=%s", exit_code, output)

    return {
        "project_id": project_id,
        "container_path": container_target,
        "files": os.listdir(extract_dir),
    }


async def export_project(session_id: str, container_db_id: str, project_id: str) -> Path | None:
    """Pack project files from container into a downloadable zip."""
    _ensure_dirs()
    container = await get_container(container_db_id)
    if not container:
        return None

    export_zip = EXPORT_DIR / f"{project_id}-export.zip"
    source_dir = UPLOAD_DIR / project_id

    if not source_dir.exists():
        return None

    with zipfile.ZipFile(export_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(source_dir):
            for f in files:
                full = Path(root) / f
                arcname = full.relative_to(source_dir)
                zf.write(full, arcname)

    return export_zip
