from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "skills_bundle.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("shotwright_skills_bundle_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


module = _load_module()


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        start = self._offset
        end = min(start + size, len(self._payload))
        self._offset = end
        return self._payload[start:end]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_read_json_applies_url_proxy_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeOpener:
        def open(self, request, timeout=None):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(module, "_build_opener", lambda **kwargs: FakeOpener())

    payload = module._read_json(
        "https://api.github.com/repos/example/repo/releases/tags/v1",
        verify_ssl=True,
        proxy=None,
        url_proxy_prefix="https://proxy.byted.org.cn",
    )

    assert payload == {"ok": True}
    assert seen["url"] == "https://proxy.byted.org.cn/https://api.github.com/repos/example/repo/releases/tags/v1"


def test_download_file_applies_url_proxy_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    class FakeOpener:
        def open(self, request, timeout=None):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            return _FakeResponse(b"bundle-bytes")

    monkeypatch.setattr(module, "_build_opener", lambda **kwargs: FakeOpener())
    monkeypatch.setattr(module, "_probe_download_capabilities", lambda *args, **kwargs: (None, False))
    destination = tmp_path / "bundle.zip"

    module._download_file(
        "https://api.github.com/repos/example/repo/releases/assets/1",
        destination,
        verify_ssl=True,
        proxy=None,
        url_proxy_prefix="https://proxy.byted.org.cn/",
    )

    assert destination.read_bytes() == b"bundle-bytes"
    assert seen["url"] == "https://proxy.byted.org.cn/https://api.github.com/repos/example/repo/releases/assets/1"


def test_plan_download_ranges_balances_segments() -> None:
    assert module._plan_download_ranges(10, 3) == [(0, 3), (4, 7), (8, 9)]


def test_download_file_uses_parallel_strategy_when_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "bundle.zip"
    seen: dict[str, object] = {"parallel": False, "stream": False}

    monkeypatch.setattr(module, "_probe_download_capabilities", lambda *args, **kwargs: (32 * 1024 * 1024, True))

    def fake_parallel(*args, **kwargs):
        seen["parallel"] = True
        destination.write_bytes(b"parallel")

    def fake_stream(*args, **kwargs):
        seen["stream"] = True

    monkeypatch.setattr(module, "_parallel_download_to_file", fake_parallel)
    monkeypatch.setattr(module, "_stream_download_to_file", fake_stream)

    module._download_file(
        "https://example.invalid/bundle.zip",
        destination,
        verify_ssl=True,
        proxy=None,
        url_proxy_prefix=None,
        download_concurrency=4,
        show_progress=False,
    )

    assert seen == {"parallel": True, "stream": False}
    assert destination.read_bytes() == b"parallel"


def test_download_file_falls_back_to_stream_when_range_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "bundle.zip"
    seen: dict[str, object] = {"parallel": False, "stream": False}

    monkeypatch.setattr(module, "_probe_download_capabilities", lambda *args, **kwargs: (None, False))

    def fake_parallel(*args, **kwargs):
        seen["parallel"] = True

    def fake_stream(*args, **kwargs):
        seen["stream"] = True
        destination.write_bytes(b"stream")

    monkeypatch.setattr(module, "_parallel_download_to_file", fake_parallel)
    monkeypatch.setattr(module, "_stream_download_to_file", fake_stream)

    module._download_file(
        "https://example.invalid/bundle.zip",
        destination,
        verify_ssl=True,
        proxy=None,
        url_proxy_prefix=None,
        download_concurrency=4,
        show_progress=False,
    )

    assert seen == {"parallel": False, "stream": True}
    assert destination.read_bytes() == b"stream"


def test_ensure_skills_bundle_uses_local_repo_tree_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    skill_root = repo_root / ".github" / "skills" / "after-effects-scripting-guide"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: after-effects-scripting-guide\ndescription: Use local checked-out skills.\n---\n",
        encoding="utf-8",
    )

    def fail_download(*_args, **_kwargs):
        raise AssertionError("ensure_skills_bundle should not download when local repo skills exist")

    monkeypatch.setattr(module, "_resolve_asset_downloads", fail_download)

    result = module.ensure_skills_bundle(
        source_repo_root=repo_root,
        install_root=repo_root,
        github_token=None,
    )

    assert result["status"] == "already-present"
    assert result["skillsRoot"] == str(repo_root / ".github" / "skills")