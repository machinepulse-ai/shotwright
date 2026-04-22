from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import project_manager as module


class FakeProjectCollection:
    def __init__(self, doc: dict) -> None:
        self.doc = doc

    async def update_one(self, _query: dict, update: dict) -> None:
        self.doc.update(update.get("$set", {}))


@pytest.mark.asyncio
async def test_refresh_project_files_loads_compositions_from_metadata_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "project-1"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "scene.aep").write_bytes(b"aep")
    (workspace_dir / module.PROJECT_METADATA_FILENAME).write_text(
        json.dumps(
            {
                "updated_at": "2026-04-22T12:00:00+00:00",
                "compositions": [
                    {
                        "name": "Main",
                        "width": 1920,
                        "height": 1080,
                        "duration_seconds": 10.0,
                        "frame_rate": 30.0,
                        "layer_count": 4,
                    },
                    {
                        "name": "Detail",
                        "width": 960,
                        "height": 540,
                        "duration_seconds": 3.0,
                        "frame_rate": 30.0,
                        "layer_count": 2,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    project_doc = {
        "_id": "project-1",
        "session_id": "session-1",
        "filename": "scene.aep",
        "workspace_dir": str(workspace_dir),
        "aep_files": [],
        "entry_aep_file": None,
        "compositions": [],
        "composition_catalog_updated_at": None,
    }
    fake_collection = FakeProjectCollection(project_doc)

    monkeypatch.setattr(module, "get_project_collection", lambda: fake_collection)

    async def fake_get_project(_session_id: str, _project_id: str) -> dict:
        return fake_collection.doc

    async def fake_publish_context_refresh(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(module, "get_project", fake_get_project)
    monkeypatch.setattr(module, "publish_context_refresh", fake_publish_context_refresh)

    refreshed = await module.refresh_project_files("session-1", "project-1")

    assert refreshed is not None
    assert refreshed["aep_files"] == ["scene.aep"]
    assert refreshed["entry_aep_file"] == "scene.aep"
    assert [item["name"] for item in refreshed["compositions"]] == ["Detail", "Main"]
    assert refreshed["composition_catalog_updated_at"] == "2026-04-22T12:00:00+00:00"