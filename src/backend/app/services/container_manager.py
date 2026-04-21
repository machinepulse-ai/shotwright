"""Docker container lifecycle management for shotwright AE instances."""

import io
import logging
import os
import tarfile
import time
from datetime import datetime, timezone
from pathlib import PureWindowsPath
from uuid import uuid4

import docker
from docker.errors import DockerException, NotFound

from app.config import settings
from app.database import get_container_collection, get_session_collection
from app.models.container import ContainerStatus
from app.services.session_streams import publish_context_refresh, publish_session_updated

logger = logging.getLogger(__name__)

_docker_client: docker.DockerClient | None = None


def _get_docker() -> docker.DockerClient:
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.DockerClient(base_url=settings.docker_host)
    return _docker_client


def _resolve_payload_mount_source() -> str | None:
    explicit_source = settings.container_payload_mount_source.strip()
    if explicit_source:
        return explicit_source

    if not settings.container_payload_mount_auto_detect:
        return None

    default_source = f"{settings.container_data_root}\\payload"
    if os.path.isdir(default_source):
        return default_source

    return None


def _uses_preinstalled_runtime_image(image: str) -> bool:
    normalized = image.strip().lower()
    return normalized.endswith(":runtime")


def _build_container_environment(image: str) -> dict[str, str]:
    if _uses_preinstalled_runtime_image(image):
        return {"AUTO_INSTALL_AFTER_EFFECTS": "0"}

    return {"AUTO_INSTALL_AFTER_EFFECTS": "1"}


async def create_container(session_id: str, image: str | None = None) -> dict:
    """Create and start a new shotwright Windows container."""
    image = image or settings.shotwright_image
    client = _get_docker()

    volume_mounts = {
        settings.shared_uploads_volume: {
            "bind": "C:\\data\\uploads",
            "mode": "rw",
        },
        settings.shared_exports_volume: {
            "bind": "C:\\data\\exports",
            "mode": "rw",
        },
        settings.shared_hls_volume: {
            "bind": "C:\\data\\hls",
            "mode": "rw",
        },
    }
    payload_mount_source = _resolve_payload_mount_source()
    if payload_mount_source:
        volume_mounts[payload_mount_source] = {
            "bind": "C:\\data\\payload",
            "mode": "ro",
        }

    run_kwargs = {
        "image": image,
        "detach": True,
        "name": f"shotwright-{session_id[:8]}-{uuid4().hex[:6]}",
        "environment": _build_container_environment(image),
        "volumes": volume_mounts,
        "isolation": "process",
    }
    if settings.container_network:
        run_kwargs["network"] = settings.container_network

    container = client.containers.run(**run_kwargs)

    doc = {
        "_id": str(uuid4()),
        "docker_id": container.id,
        "session_id": session_id,
        "image": image,
        "status": ContainerStatus.running.value,
        "created_at": datetime.now(timezone.utc),
        "ports": {},
    }
    col = get_container_collection()
    await col.insert_one(doc)
    await get_session_collection().update_one(
        {"_id": session_id},
        {
            "$set": {
                "container_id": doc["_id"],
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    await publish_session_updated(session_id)
    await publish_context_refresh(session_id, "container.created", container_id=doc["_id"])
    return doc


async def stop_container(container_db_id: str) -> dict | None:
    """Stop a running container."""
    col = get_container_collection()
    doc = await col.find_one({"_id": container_db_id})
    if not doc:
        return None
    try:
        client = _get_docker()
        c = client.containers.get(doc["docker_id"])
        c.stop(timeout=30)
    except NotFound:
        pass
    except DockerException as exc:
        logger.warning("Failed to stop container %s: %s", doc["docker_id"], exc)

    await col.update_one(
        {"_id": container_db_id},
        {"$set": {"status": ContainerStatus.stopped.value}},
    )
    await get_session_collection().update_one(
        {"container_id": container_db_id},
        {"$set": {"updated_at": datetime.now(timezone.utc)}},
    )
    doc["status"] = ContainerStatus.stopped.value
    await publish_session_updated(doc["session_id"])
    await publish_context_refresh(doc["session_id"], "container.stopped", container_id=container_db_id)
    return doc


async def remove_container(container_db_id: str) -> bool:
    """Remove a container and its DB record."""
    col = get_container_collection()
    doc = await col.find_one({"_id": container_db_id})
    if not doc:
        return False
    try:
        client = _get_docker()
        c = client.containers.get(doc["docker_id"])
        c.remove(force=True)
    except NotFound:
        pass
    except DockerException as exc:
        logger.warning("Failed to remove container %s: %s", doc["docker_id"], exc)

    await col.delete_one({"_id": container_db_id})
    await get_session_collection().update_one(
        {"container_id": container_db_id},
        {
            "$set": {
                "container_id": None,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    await publish_session_updated(doc["session_id"])
    await publish_context_refresh(doc["session_id"], "container.removed", container_id=container_db_id)
    return True


async def list_containers(session_id: str | None = None) -> list[dict]:
    """List containers, optionally filtered by session."""
    col = get_container_collection()
    query = {}
    if session_id:
        query["session_id"] = session_id
    return await col.find(query).to_list(length=200)


async def get_container(container_db_id: str) -> dict | None:
    col = get_container_collection()
    return await col.find_one({"_id": container_db_id})


async def get_session_container(session_id: str) -> dict | None:
    col = get_container_collection()
    return await col.find_one({"session_id": session_id, "status": ContainerStatus.running.value})


async def exec_in_container(docker_id: str, cmd: list[str]) -> tuple[int, str]:
    """Execute a command inside a running container. Returns (exit_code, output)."""
    client = _get_docker()
    container = client.containers.get(docker_id)
    result = container.exec_run(cmd, demux=False)
    output = result.output.decode("utf-8", errors="replace") if result.output else ""
    return result.exit_code, output


async def put_text_files_in_container(docker_id: str, files: dict[str, str], *, encoding: str = "utf-8") -> None:
    """Write one or more UTF-8 text files into an existing container via the Docker archive API."""
    if not files:
        return

    grouped_files: dict[str, list[tuple[str, str]]] = {}
    for raw_path, content in files.items():
        container_path = PureWindowsPath(raw_path)
        parent_dir = str(container_path.parent)
        grouped_files.setdefault(parent_dir, []).append((container_path.name, content))

    client = _get_docker()
    container = client.containers.get(docker_id)

    for parent_dir, group in grouped_files.items():
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar_handle:
            for filename, content in group:
                data = content.encode(encoding)
                tar_info = tarfile.TarInfo(name=filename)
                tar_info.size = len(data)
                tar_info.mtime = int(time.time())
                tar_handle.addfile(tar_info, io.BytesIO(data))

        archive.seek(0)
        success = container.put_archive(parent_dir, archive.read())
        if not success:
            raise RuntimeError(f"Failed to write files into container directory {parent_dir}")
