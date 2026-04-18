"""Pydantic schemas for admin operations."""

from pydantic import BaseModel, Field


class AdminLogin(BaseModel):
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GithubTokenUpdate(BaseModel):
    github_token: str = ""


class CopilotSettingsUpdate(BaseModel):
    copilot_cli_path: str = ""
    copilot_workspace_root: str = Field(min_length=1)
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""


class AdminSettings(BaseModel):
    github_token_set: bool = False
    copilot_cli_path: str = ""
    copilot_workspace_root: str = "C:\\workspace"
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""
