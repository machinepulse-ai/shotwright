"""Schemas for session-scoped reference videos and storyboard images."""

from datetime import datetime

from pydantic import BaseModel, Field


class ReferenceVideoInfo(BaseModel):
    id: str
    session_id: str
    filename: str
    file_path: str
    shared_relative_path: str
    mime_type: str | None = None
    size_bytes: int = Field(ge=1)
    duration_seconds: float = Field(ge=0.0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    thumbnail_path: str | None = None
    thumbnail_shared_relative_path: str | None = None
    thumbnail_mime_type: str | None = None
    created_at: datetime


class StoryboardInfo(BaseModel):
    id: str
    session_id: str
    filename: str
    file_path: str
    shared_relative_path: str
    mime_type: str | None = None
    created_at: datetime
    source_video_path: str
    source_video_relative_path: str
    source_video_filename: str
    source_video_duration_seconds: float = Field(ge=0.0)
    source_video_width: int | None = Field(default=None, ge=1)
    source_video_height: int | None = Field(default=None, ge=1)
    clip_start_seconds: float = Field(ge=0.0)
    clip_end_seconds: float = Field(ge=0.0)
    clip_duration_seconds: float = Field(ge=0.0)
    interval_seconds: float = Field(gt=0.0)
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    tile_width: int = Field(ge=64)
    estimated_frames: int = Field(ge=1)
    crop: dict[str, int] | None = None
    ffmpeg_filter: str


class RenderOutputInfo(BaseModel):
    id: str
    session_id: str
    project_id: str
    filename: str
    file_path: str
    shared_relative_path: str
    mime_type: str | None = None
    size_bytes: int = Field(ge=1)
    created_at: datetime
    composition: str
    aep_path: str
    aep_file: str | None = None
    project_workspace_dir: str | None = None
    work_dir: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    stream_id: str | None = None
    playlist_url: str | None = None
    thumbnail_path: str | None = None
