"""Schemas for uploaded AEP project archives."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    uploaded = "uploaded"
    active = "active"
    exported = "exported"


class ProjectCompositionInfo(BaseModel):
    name: str
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    duration_seconds: float | None = Field(default=None, ge=0.0)
    frame_rate: float | None = Field(default=None, gt=0.0)
    layer_count: int | None = Field(default=None, ge=0)


class ProjectInfo(BaseModel):
    id: str = Field(alias="_id")
    session_id: str
    filename: str
    workspace_dir: str
    aep_files: list[str] = Field(default_factory=list)
    entry_aep_file: str | None = None
    compositions: list[ProjectCompositionInfo] = Field(default_factory=list)
    composition_catalog_updated_at: datetime | None = None
    origin: str = "uploaded"
    created_at: datetime
    status: ProjectStatus = ProjectStatus.uploaded

    model_config = {"populate_by_name": True}
