"""Container lifecycle router."""

from fastapi import APIRouter, HTTPException

from app.models.container import ContainerCreate, ContainerInfo
from app.services import container_manager as cm

router = APIRouter(prefix="/containers", tags=["containers"])


@router.post("", response_model=ContainerInfo, status_code=201)
async def create_container(body: ContainerCreate):
    doc = await cm.create_container(body.session_id, body.image)
    return doc


@router.get("", response_model=list[ContainerInfo])
async def list_containers(session_id: str | None = None):
    return await cm.list_containers(session_id)


@router.get("/{container_id}", response_model=ContainerInfo)
async def get_container(container_id: str):
    doc = await cm.get_container(container_id)
    if not doc:
        raise HTTPException(404, "Container not found")
    return doc


@router.post("/{container_id}/stop", response_model=ContainerInfo)
async def stop_container(container_id: str):
    doc = await cm.stop_container(container_id)
    if not doc:
        raise HTTPException(404, "Container not found")
    return doc


@router.delete("/{container_id}", status_code=204)
async def remove_container(container_id: str):
    ok = await cm.remove_container(container_id)
    if not ok:
        raise HTTPException(404, "Container not found")
