"""Pydantic schemas for agent sessions."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    idle = "idle"
    running = "running"
    awaiting_input = "awaiting_input"
    error = "error"
    closed = "closed"


class SessionCreate(BaseModel):
    name: str = Field(default="Untitled Session", max_length=128)


class SessionInDB(BaseModel):
    id: str = Field(alias="_id")
    name: str
    status: SessionStatus = SessionStatus.idle
    copilot_session_id: str | None = None
    container_id: str | None = None
    active_project_id: str | None = None
    latest_render_path: str | None = None
    latest_stream_url: str | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True}


class SessionUpdate(BaseModel):
    name: str | None = None
    status: SessionStatus | None = None
    copilot_session_id: str | None = None
    container_id: str | None = None
    active_project_id: str | None = None
    latest_render_path: str | None = None
    latest_stream_url: str | None = None
    last_error: str | None = None
