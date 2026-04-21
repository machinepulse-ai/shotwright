from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services import agent_tools as module


class FakeAsyncCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def sort(self, field: str, direction: int):
        self._docs.sort(key=lambda doc: doc.get(field), reverse=direction < 0)
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeMessageCollection:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def find(self, *_args, **_kwargs):
        return FakeAsyncCursor(self._docs)


def test_build_empty_project_jsx_saves_and_quits() -> None:
    script = module._build_empty_project_jsx()

    assert 'SHOTWRIGHT_PROJECT_FILE' in script
    assert 'app.project.save(new File(projectFile));' in script
    assert 'app.quit();' in script


def test_build_reference_composition_jsx_includes_reference_asset_and_comp_settings() -> None:
    script = module._build_reference_composition_jsx(
        reference_asset_path='C:/data/uploads/session/project/assets/references/hero.png',
        composition_name='Main',
        width=1920,
        height=1080,
        duration_seconds=10.0,
        frame_rate=30.0,
        fit_mode='cover',
        reset_existing=False,
    )

    assert 'new File("C:/data/uploads/session/project/assets/references/hero.png")' in script
    assert 'var compositionName = "Main";' in script
    assert 'imageLayer.name = "shotwright_reference_image";' in script
    assert 'comp = app.project.items.addComp' in script


@pytest.mark.asyncio
async def test_list_session_image_attachments_filters_missing_and_dedupes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    existing_a = tmp_path / 'existing-a.png'
    existing_a.write_bytes(b'a')
    existing_b = tmp_path / 'existing-b.png'
    existing_b.write_bytes(b'b')
    missing = tmp_path / 'missing.png'

    docs = [
        {
            'created_at': datetime(2026, 4, 21, 13, 10, tzinfo=timezone.utc),
            'metadata': {
                'attachments': [
                    {'type': 'image', 'file_path': str(existing_a), 'display_name': 'first.png'},
                    {'type': 'image', 'file_path': str(missing), 'display_name': 'missing.png'},
                ]
            },
        },
        {
            'created_at': datetime(2026, 4, 21, 13, 11, tzinfo=timezone.utc),
            'metadata': {
                'attachments': [
                    {'type': 'image', 'file_path': str(existing_b), 'display_name': 'second.png'},
                    {'type': 'image', 'file_path': str(existing_a), 'display_name': 'duplicate.png'},
                ]
            },
        },
    ]

    monkeypatch.setattr(module, 'get_message_collection', lambda: FakeMessageCollection(docs))

    attachments = await module._list_session_image_attachments('session-1', limit=5)

    assert [attachment['display_name'] for attachment in attachments] == ['second.png', 'duplicate.png']
    assert [attachment['file_path'] for attachment in attachments] == [str(existing_b), str(existing_a)]


@pytest.mark.asyncio
async def test_stage_session_image_attachments_copies_into_project_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_image = tmp_path / 'uploads' / 'reference.png'
    source_image.parent.mkdir(parents=True, exist_ok=True)
    source_image.write_bytes(b'reference')

    async def fake_list_session_image_attachments(_session_id: str, *, limit: int = 8) -> list[dict]:
        return [
            {
                'file_path': str(source_image),
                'display_name': 'reference.png',
            }
        ][:limit]

    monkeypatch.setattr(module, '_list_session_image_attachments', fake_list_session_image_attachments)

    project_root = tmp_path / 'project'
    project_root.mkdir(parents=True, exist_ok=True)
    project = {'workspace_dir': str(project_root)}

    staged = await module._stage_session_image_attachments(
        'session-1',
        project,
        latest_only=True,
        asset_name='hero-reference.png',
    )

    assert len(staged) == 1
    staged_asset = staged[0]
    assert staged_asset['project_relative_path'] == 'assets/references/hero-reference.png'
    assert Path(staged_asset['project_asset_path']).read_bytes() == b'reference'