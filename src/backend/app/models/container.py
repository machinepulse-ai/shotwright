"""Pydantic schemas for container instances."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ContainerStatus(str, Enum):
    creating = "creating"
    running = "running"
    stopped = "stopped"
    error = "error"
    removed = "removed"


class ContainerCreate(BaseModel):
    session_id: str
    image: str | None = None  # defaults to shotwright_image from config


class ContainerInfo(BaseModel):
    id: str = Field(alias="_id")
    docker_id: str
    session_id: str
    image: str
    status: ContainerStatus
    created_at: datetime
    ports: dict[str, int] = {}

    model_config = {"populate_by_name": True}
