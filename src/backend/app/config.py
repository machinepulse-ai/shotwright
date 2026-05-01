"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- App ---
    app_name: str = "Shotwright API"
    debug: bool = False
    secret_key: str = "change-me-in-production"
    admin_password: str = "admin"

    # --- MongoDB ---
    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db: str = "shotwright"

    # --- Docker ---
    docker_host: str = "npipe:////./pipe/docker_engine"
    shotwright_image: str = "shotwright:runtime"
    container_isolation: str = "process"
    container_network: str = ""
    container_data_root: str = "C:\\data"
    container_payload_mount_source: str = ""
    container_payload_mount_auto_detect: bool = False
    shared_uploads_volume: str = "shotwright_uploads"
    shared_exports_volume: str = "shotwright_exports"
    shared_hls_volume: str = "shotwright_hls"

    # --- Copilot ---
    agent_provider: str = "copilot"
    github_token: str = ""
    copilot_model: str = "gpt-5.4"
    copilot_reasoning_effort: str = "high"
    copilot_cli_path: str = ""
    copilot_workspace_root: str = "C:\\workspace"
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""
    copilot_turn_timeout_seconds: float = 900.0

    # --- Codex bridge ---
    openai_api_key: str = ""
    codex_node_path: str = ""
    codex_bridge_script: str = ""
    codex_path_override: str = ""
    codex_runtime_home: str = ""
    codex_base_url: str = ""
    codex_model: str = "gpt-5.4"
    codex_reasoning_effort: str = "high"
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

    # --- Paths ---
    upload_dir: str = "C:\\data\\uploads"
    export_dir: str = "C:\\data\\exports"
    hls_dir: str = "C:\\data\\hls"

    # --- Python agent tool runtime ---
    python_tool_auto_sync_dependencies: bool = False
    python_tool_runtime_dir: str = "C:\\data\\python"
    python_tool_venv_dir: str = ""
    python_tool_requirements: str = ""
    python_tool_pip_cache_dir: str = ""
    python_tool_system_site_packages: bool = True
    python_tool_dependency_sync_timeout_seconds: int = 1800

    # --- Future: Redis ---
    # redis_uri: str = "redis://redis:6379/0"

    # --- Future: PostgreSQL ---
    # pg_uri: str = "postgresql+asyncpg://user:pass@pg:5432/shotwright"

    model_config = {"env_prefix": "SHOTWRIGHT_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
