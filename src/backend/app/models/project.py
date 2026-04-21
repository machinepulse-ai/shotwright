"""Schemas for uploaded AEP project archives."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    uploaded = "uploaded"
    active = "active"
    exported = "exported"


class ProjectInfo(BaseModel):
    id: str = Field(alias="_id")
    session_id: str
    filename: str
    workspace_dir: str
    aep_files: list[str] = Field(default_factory=list)
    entry_aep_file: str | None = None
    origin: str = "uploaded"
    created_at: datetime
    status: ProjectStatus = ProjectStatus.uploaded

    model_config = {"populate_by_name": True}
