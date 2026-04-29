from __future__ import annotations

import json

from app.services import codex_config


def test_local_codex_profile_initializes_runtime_defaults(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        """
model_provider = "proxy"
preferred_auth_method = "apikey"
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
model_max_output_tokens = 64000

[model_providers.proxy]
name = "byted"
auth_mode = "api"
base_url = "http://codex.internal.test/v1"
wire_api = "responses"
""",
        encoding="utf-8",
    )
    (codex_home / "auth.json").write_text(
        json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "secret-key"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    profile = codex_config.load_local_codex_profile()
    runtime_settings = codex_config.resolve_codex_runtime_settings({})

    assert profile["model_provider"] == "proxy"
    assert profile["api_key_set"] is True
    assert profile["base_url"] == "http://codex.internal.test/v1"
    assert runtime_settings["codex_model"] == "gpt-5.5"
    assert runtime_settings["codex_reasoning_effort"] == "xhigh"
    assert runtime_settings["codex_base_url"] == "http://codex.internal.test/v1"
    assert codex_config.resolve_openai_api_key({}) == "secret-key"


def test_codex_sdk_config_does_not_serialize_api_key(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        """
model = "gpt-5.5"
OPENAI_API_KEY = "do-not-forward"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    sdk_config = codex_config.build_codex_sdk_config()

    assert sdk_config["model"] == "gpt-5.5"
    assert "OPENAI_API_KEY" not in sdk_config
