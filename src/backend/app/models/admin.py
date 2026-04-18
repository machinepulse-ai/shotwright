"""Pydantic schemas for admin operations."""

from pydantic import BaseModel, Field

from app.models.session import ReasoningEffort


class AdminLogin(BaseModel):
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GithubTokenUpdate(BaseModel):
    github_token: str = ""


class CopilotSettingsUpdate(BaseModel):
    default_copilot_model: str = Field(default="gpt-5.4", min_length=1)
    default_copilot_reasoning_effort: ReasoningEffort | None = "high"
    copilot_cli_path: str = ""
    copilot_workspace_root: str = Field(min_length=1)
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""


class AdminSettings(BaseModel):
    github_token_set: bool = False
    default_copilot_model: str = "gpt-5.4"
    default_copilot_reasoning_effort: ReasoningEffort | None = "high"
    copilot_cli_path: str = ""
    copilot_workspace_root: str = "C:\\workspace"
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""
