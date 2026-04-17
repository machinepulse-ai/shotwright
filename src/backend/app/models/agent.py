"""Pydantic schemas for agent actions."""

from enum import Enum

from pydantic import BaseModel, Field


class AgentAction(str, Enum):
    start_container = "start_container"
    upload_project = "upload_project"
    export_project = "export_project"
    render_video = "render_video"
    run_jsx = "run_jsx"


class AgentCommand(BaseModel):
    session_id: str
    action: AgentAction
    payload: dict = Field(default_factory=dict)


class AgentResponse(BaseModel):
    success: bool
    action: AgentAction
    message: str
    data: dict = Field(default_factory=dict)


class JsxScript(BaseModel):
    session_id: str
    script_content: str = Field(min_length=1)
    description: str = ""
