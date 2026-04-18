"""Pydantic schemas for admin operations."""

from typing import Literal

from pydantic import BaseModel, Field


ReasoningEffort = Literal["low", "medium", "high", "xhigh"]


class AdminLogin(BaseModel):
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GithubTokenUpdate(BaseModel):
    github_token: str = ""


class CopilotSettingsUpdate(BaseModel):
    copilot_model: str = Field(min_length=1)
    copilot_reasoning_effort: ReasoningEffort = "high"
    copilot_cli_path: str = ""
    copilot_workspace_root: str = Field(min_length=1)
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""


class AdminSettings(BaseModel):
    github_token_set: bool = False
    copilot_model: str = "gpt-5.4"
    copilot_reasoning_effort: ReasoningEffort = "high"
    copilot_cli_path: str = ""
    copilot_workspace_root: str = "C:\\workspace"
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""


class PublicRuntimeSettings(BaseModel):
    copilot_model: str
    copilot_reasoning_effort: ReasoningEffort
