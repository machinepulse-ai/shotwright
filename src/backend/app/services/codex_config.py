"""Codex provider configuration helpers."""

from __future__ import annotations

import json
import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.config import settings

_CODEX_DIR_NAME = ".codex"
_SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _configured_setting(name: str) -> Any | None:
    if name in settings.model_fields_set:
        value = getattr(settings, name)
        field = settings.__class__.model_fields.get(name)
        if field is not None and value == field.default:
            return None
        return value
    return None


def _codex_home_candidates() -> list[Path]:
    candidates: list[Path] = []
    for raw_candidate in (
        os.environ.get("CODEX_HOME"),
        os.environ.get("SHOTWRIGHT_CODEX_HOME"),
        str(Path(os.environ["USERPROFILE"]) / _CODEX_DIR_NAME) if os.environ.get("USERPROFILE") else None,
        str(Path.home() / _CODEX_DIR_NAME),
        r"C:\Users\root\.codex",
    ):
        candidate = _first_non_empty(raw_candidate)
        if not candidate:
            continue
        path = Path(candidate)
        if path not in candidates:
            candidates.append(path)
    return candidates


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def load_local_codex_profile() -> dict[str, Any]:
    """Read the local Codex CLI profile without exposing secret values to API responses."""

    config: dict[str, Any] = {}
    auth: dict[str, Any] = {}
    home_path = ""
    for codex_home in _codex_home_candidates():
        config_path = codex_home / "config.toml"
        auth_path = codex_home / "auth.json"
        if not config and config_path.is_file():
            config = _read_toml(config_path)
            home_path = str(codex_home)
        if not auth and auth_path.is_file():
            auth = _read_json(auth_path)
            home_path = home_path or str(codex_home)
        if config and auth:
            break

    model_provider = _first_non_empty(config.get("model_provider"))
    provider_config = {}
    model_providers = config.get("model_providers")
    if isinstance(model_providers, dict) and model_provider:
        raw_provider_config = model_providers.get(model_provider)
        if isinstance(raw_provider_config, dict):
            provider_config = raw_provider_config

    api_key = _first_non_empty(auth.get("OPENAI_API_KEY"), auth.get("openai_api_key"))
    return {
        "home_path": home_path,
        "config": config,
        "auth_mode": _first_non_empty(auth.get("auth_mode"), config.get("preferred_auth_method")),
        "api_key": api_key,
        "api_key_set": bool(api_key),
        "model": _first_non_empty(config.get("model")),
        "model_reasoning_effort": _first_non_empty(config.get("model_reasoning_effort")),
        "model_provider": model_provider,
        "provider_config": provider_config,
        "base_url": _first_non_empty(provider_config.get("base_url"), config.get("base_url")),
    }


def build_codex_sdk_config(local_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = local_profile or load_local_codex_profile()
    config = profile.get("config")
    if not isinstance(config, dict):
        return {}

    sdk_config = deepcopy(config)
    # Runtime credentials are passed through apiKey/env, never through this
    # serialized config object.
    sdk_config.pop("OPENAI_API_KEY", None)
    sdk_config.pop("openai_api_key", None)
    return sdk_config


def resolve_openai_api_key(admin_doc: dict[str, Any] | None = None) -> str:
    doc = admin_doc or {}
    local_profile = load_local_codex_profile()
    return _first_non_empty(
        doc.get("openai_api_key"),
        _configured_setting("openai_api_key"),
        os.environ.get("OPENAI_API_KEY"),
        local_profile.get("api_key"),
    )


def is_openai_api_key_set(admin_doc: dict[str, Any] | None = None) -> bool:
    return bool(resolve_openai_api_key(admin_doc))


def resolve_agent_provider(admin_doc: dict[str, Any] | None = None) -> str:
    provider = _first_non_empty(
        (admin_doc or {}).get("agent_provider"),
        _configured_setting("agent_provider"),
        settings.agent_provider,
    ).lower()
    return provider if provider in {"copilot", "codex"} else "copilot"


def _resolve_bool(
    name: str,
    admin_doc: dict[str, Any],
    *,
    default: bool,
) -> bool:
    if name in admin_doc:
        return bool(admin_doc[name])
    configured = _configured_setting(name)
    if configured is not None:
        return bool(configured)
    return default


def _resolve_float(
    name: str,
    admin_doc: dict[str, Any],
    *,
    default: float,
) -> float:
    raw_value = admin_doc.get(name, _configured_setting(name))
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def resolve_codex_runtime_settings(admin_doc: dict[str, Any] | None = None) -> dict[str, str | bool | float]:
    doc = admin_doc or {}
    local_profile = load_local_codex_profile()
    configured_model = _configured_setting("codex_model")
    configured_reasoning = _configured_setting("codex_reasoning_effort")
    configured_base_url = _configured_setting("codex_base_url")
    codex_reasoning_effort = _first_non_empty(
        doc.get("codex_reasoning_effort"),
        configured_reasoning,
        local_profile.get("model_reasoning_effort"),
        settings.codex_reasoning_effort,
    )
    if codex_reasoning_effort not in _SUPPORTED_REASONING_EFFORTS:
        codex_reasoning_effort = "high"

    return {
        "codex_node_path": _first_non_empty(doc.get("codex_node_path"), _configured_setting("codex_node_path")),
        "codex_bridge_script": _first_non_empty(
            doc.get("codex_bridge_script"),
            _configured_setting("codex_bridge_script"),
        ),
        "codex_path_override": _first_non_empty(
            doc.get("codex_path_override"),
            _configured_setting("codex_path_override"),
        ),
        "codex_base_url": _first_non_empty(
            doc.get("codex_base_url"),
            configured_base_url,
            local_profile.get("base_url"),
            settings.codex_base_url,
        ),
        "codex_model": _first_non_empty(
            doc.get("codex_model"),
            configured_model,
            local_profile.get("model"),
            settings.codex_model,
        ),
        "codex_reasoning_effort": codex_reasoning_effort,
        "codex_turn_timeout_seconds": _resolve_float(
            "codex_turn_timeout_seconds",
            doc,
            default=settings.codex_turn_timeout_seconds,
        ),
        "codex_workspace_root": _first_non_empty(
            doc.get("codex_workspace_root"),
            _configured_setting("codex_workspace_root"),
            settings.codex_workspace_root,
            settings.copilot_workspace_root,
        ),
        "codex_approval_policy": _first_non_empty(
            doc.get("codex_approval_policy"),
            _configured_setting("codex_approval_policy"),
            settings.codex_approval_policy,
        ),
        "codex_sandbox_mode": _first_non_empty(
            doc.get("codex_sandbox_mode"),
            _configured_setting("codex_sandbox_mode"),
            settings.codex_sandbox_mode,
        ),
        "codex_network_access_enabled": _resolve_bool(
            "codex_network_access_enabled",
            doc,
            default=settings.codex_network_access_enabled,
        ),
        "codex_skip_git_repo_check": _resolve_bool(
            "codex_skip_git_repo_check",
            doc,
            default=settings.codex_skip_git_repo_check,
        ),
        "codex_web_search_mode": _first_non_empty(
            doc.get("codex_web_search_mode"),
            _configured_setting("codex_web_search_mode"),
            settings.codex_web_search_mode,
        ),
        "codex_http_proxy": _first_non_empty(
            doc.get("codex_http_proxy"),
            _configured_setting("codex_http_proxy"),
            settings.codex_http_proxy,
            os.environ.get("HTTP_PROXY"),
            os.environ.get("http_proxy"),
        ),
        "codex_https_proxy": _first_non_empty(
            doc.get("codex_https_proxy"),
            _configured_setting("codex_https_proxy"),
            settings.codex_https_proxy,
            os.environ.get("HTTPS_PROXY"),
            os.environ.get("https_proxy"),
        ),
        "codex_no_proxy": _first_non_empty(
            doc.get("codex_no_proxy"),
            _configured_setting("codex_no_proxy"),
            settings.codex_no_proxy,
            os.environ.get("NO_PROXY"),
            os.environ.get("no_proxy"),
        ),
    }
