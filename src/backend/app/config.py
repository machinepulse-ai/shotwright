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
    shotwright_image: str = "shotwright:latest"
    container_network: str = ""
    container_data_root: str = "C:\\data"
    shared_uploads_volume: str = "shotwright_uploads"
    shared_exports_volume: str = "shotwright_exports"
    shared_hls_volume: str = "shotwright_hls"

    # --- Copilot ---
    github_token: str = ""
    copilot_model: str = "gpt-5.4"
    copilot_reasoning_effort: str = "high"
    copilot_cli_path: str = ""
    copilot_workspace_root: str = "C:\\workspace"
    copilot_use_logged_in_user: bool = False
    copilot_http_proxy: str = ""
    copilot_https_proxy: str = ""
    copilot_no_proxy: str = ""

    # --- Paths ---
    upload_dir: str = "C:\\data\\uploads"
    export_dir: str = "C:\\data\\exports"
    hls_dir: str = "C:\\data\\hls"

    # --- Future: Redis ---
    # redis_uri: str = "redis://redis:6379/0"

    # --- Future: PostgreSQL ---
    # pg_uri: str = "postgresql+asyncpg://user:pass@pg:5432/shotwright"

    model_config = {"env_prefix": "SHOTWRIGHT_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
