"""Pydantic schemas for admin operations."""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.session import ReasoningEffort

AgentProvider = Literal["copilot", "codex"]


class AdminLogin(BaseModel):
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GithubTokenUpdate(BaseModel):
    github_token: str = ""


class OpenAIKeyUpdate(BaseModel):
    openai_api_key: str = ""


class AgentSettingsUpdate(BaseModel):
    agent_provider: AgentProvider = "copilot"
    default_copilot_model: str = Field(default="gpt-5.4", min_length=1)
    default_copilot_reasoning_effort: ReasoningEffort | None = "high"
    copilot_turn_timeout_seconds: float = Field(default=900.0, gt=0)
    copilot_cli_path: str = ""
    copilot_workspace_root: str = Field(min_length=1)
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""
    codex_node_path: str = ""
    codex_bridge_script: str = ""
    codex_path_override: str = ""
    codex_base_url: str = ""
    codex_model: str = Field(default="gpt-5.4", min_length=1)
    codex_reasoning_effort: ReasoningEffort | None = "high"
    codex_turn_timeout_seconds: float = Field(default=900.0, gt=0)
    codex_workspace_root: str = Field(min_length=1)
    codex_approval_policy: str = "never"
    codex_sandbox_mode: str = "workspace-write"
    codex_network_access_enabled: bool = False
    codex_skip_git_repo_check: bool = False
    codex_web_search_mode: str = ""
    codex_http_proxy: str = ""
    codex_https_proxy: str = ""
    codex_no_proxy: str = ""


class AdminSettings(BaseModel):
    agent_provider: AgentProvider = "copilot"
    github_token_set: bool = False
    openai_api_key_set: bool = False
    default_copilot_model: str = "gpt-5.4"
    default_copilot_reasoning_effort: ReasoningEffort | None = "high"
    copilot_turn_timeout_seconds: float = 900.0
    copilot_cli_path: str = ""
    copilot_workspace_root: str = "C:\\workspace"
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""
    codex_node_path: str = ""
    codex_bridge_script: str = ""
    codex_path_override: str = ""
    codex_base_url: str = ""
    codex_model: str = "gpt-5.4"
    codex_reasoning_effort: ReasoningEffort | None = "high"
    codex_turn_timeout_seconds: float = 900.0
    codex_workspace_root: str = "C:\\workspace"
    codex_approval_policy: str = "never"
    codex_sandbox_mode: str = "workspace-write"
    codex_network_access_enabled: bool = False
    codex_skip_git_repo_check: bool = False
    codex_web_search_mode: str = ""
    codex_http_proxy: str = ""
    codex_https_proxy: str = ""
    codex_no_proxy: str = ""
