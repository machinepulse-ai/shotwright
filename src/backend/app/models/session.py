"""Pydantic schemas for agent sessions."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


ReasoningEffort = Literal["low", "medium", "high", "xhigh"]


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
    copilot_model: str = "gpt-5.4"
    copilot_reasoning_effort: ReasoningEffort | None = "high"
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
    copilot_model: str | None = None
    copilot_reasoning_effort: ReasoningEffort | None = None
    copilot_session_id: str | None = None
    container_id: str | None = None
    active_project_id: str | None = None
    latest_render_path: str | None = None
    latest_stream_url: str | None = None
    last_error: str | None = None


class CopilotModelOption(BaseModel):
    id: str
    name: str
    supports_reasoning_effort: bool = False
    supported_reasoning_efforts: list[ReasoningEffort] = Field(default_factory=list)
    default_reasoning_effort: ReasoningEffort | None = None
