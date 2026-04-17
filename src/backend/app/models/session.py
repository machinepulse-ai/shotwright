"""Pydantic schemas for agent sessions."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    active = "active"
    idle = "idle"
    rendering = "rendering"
    closed = "closed"


class SessionCreate(BaseModel):
    name: str = Field(default="Untitled Session", max_length=128)


class SessionInDB(BaseModel):
    id: str = Field(alias="_id")
    name: str
    status: SessionStatus = SessionStatus.active
    container_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True}


class SessionUpdate(BaseModel):
    name: str | None = None
    status: SessionStatus | None = None
    container_id: str | None = None
