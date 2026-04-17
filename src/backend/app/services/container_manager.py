"""Docker container lifecycle management for shotwright AE instances."""

import logging
from datetime import datetime, timezone
from uuid import uuid4

import docker
from docker.errors import DockerException, NotFound

from app.config import settings
from app.database import get_container_collection, get_session_collection
from app.models.container import ContainerStatus

logger = logging.getLogger(__name__)

_docker_client: docker.DockerClient | None = None


def _get_docker() -> docker.DockerClient:
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.DockerClient(base_url=settings.docker_host)
    return _docker_client


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
        f"{settings.container_data_root}\\payload": {
            "bind": "C:\\data\\payload",
            "mode": "ro",
        },
    }

    run_kwargs = {
        "image": image,
        "detach": True,
        "name": f"shotwright-{session_id[:8]}-{uuid4().hex[:6]}",
        "environment": {"AUTO_INSTALL_AFTER_EFFECTS": "1"},
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
