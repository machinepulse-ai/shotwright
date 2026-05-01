from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import agent_tools as module


async def fake_python_runtime():
    return Path(sys.executable), {'enabled': False}, {}, None


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


class FakeSessionCollection:
    def __init__(self, doc: dict) -> None:
        self.doc = dict(doc)
        self.updates: list[dict] = []

    async def find_one(self, query: dict) -> dict | None:
        if query.get('_id') == self.doc.get('_id'):
            return dict(self.doc)
        return None

    async def update_one(self, _query: dict, update: dict) -> SimpleNamespace:
        self.updates.append(update)
        for key, value in (update.get('$set') or {}).items():
            self.doc[key] = value
        return SimpleNamespace(modified_count=1)


def test_build_empty_project_jsx_resets_current_project_without_modal_prompts() -> None:
    script = module._build_empty_project_jsx()

    assert 'app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);' in script
    assert 'app.newProject();' not in script
    assert 'SHOTWRIGHT_PROJECT_FILE' not in script
    assert 'app.quit();' not in script


def test_should_reuse_generated_project_workspace_only_for_unsaved_managed_workspace(tmp_path: Path) -> None:
    workspace_dir = tmp_path / 'project'
    workspace_dir.mkdir(parents=True, exist_ok=True)

    assert module._should_reuse_generated_project_workspace(
        {
            'origin': 'generated',
            'workspace_dir': str(workspace_dir),
            'entry_aep_file': 'draft.aep',
            'aep_files': [],
        }
    ) is True
    assert module._should_reuse_generated_project_workspace(
        {
            'origin': 'generated',
            'workspace_dir': str(workspace_dir),
            'entry_aep_file': 'draft.aep',
            'aep_files': ['draft.aep'],
        }
    ) is False
    assert module._should_reuse_generated_project_workspace(
        {
            'origin': 'uploaded',
            'workspace_dir': str(workspace_dir),
            'entry_aep_file': 'draft.aep',
            'aep_files': [],
        }
    ) is False


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


@pytest.mark.asyncio
async def test_inspect_workspace_serializes_recent_image_attachment_datetimes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attachment_time = datetime(2026, 4, 22, 16, 25, 29, tzinfo=timezone.utc)
    image_path = tmp_path / 'reference.png'
    image_path.write_bytes(b'png')

    session_collection = FakeSessionCollection(
        {
            '_id': 'session-1',
            'status': 'idle',
            'container_id': 'container-1',
            'active_project_id': None,
        }
    )

    async def fake_get_container(container_id: str) -> dict:
        assert container_id == 'container-1'
        return {'_id': 'container-1', 'status': 'running', 'docker_id': 'docker-1'}

    async def fake_list_projects(_session_id: str) -> list[dict]:
        return []

    async def fake_list_session_image_attachments(_session_id: str, *, limit: int = 8) -> list[dict]:
        return [
            {
                'file_path': str(image_path),
                'display_name': 'reference.png',
                'created_at': attachment_time,
            }
        ][:limit]

    monkeypatch.setattr(module, 'get_session_collection', lambda: session_collection)
    monkeypatch.setattr(module.cm, 'get_container', fake_get_container)
    monkeypatch.setattr(module.pm, 'list_projects', fake_list_projects)
    monkeypatch.setattr(module, '_list_session_image_attachments', fake_list_session_image_attachments)
    monkeypatch.setattr(module.nr, 'list_render_outputs', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module.rm, 'list_reference_videos', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module.rm, 'list_storyboards', lambda *_args, **_kwargs: [])

    tools = {tool.name: tool for tool in module.build_shotwright_tools('session-1')}
    result = await tools['inspect_workspace'].handler(SimpleNamespace(arguments={}))

    assert result.result_type == 'success'

    payload = json.loads(result.text_result_for_llm)
    assert payload['recent_image_attachments'][0]['created_at'] == attachment_time.isoformat()


@pytest.mark.asyncio
async def test_run_python_code_uses_active_project_workspace_and_reports_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'project'
    project_root.mkdir(parents=True, exist_ok=True)
    session_collection = FakeSessionCollection(
        {
            '_id': 'session-1',
            'status': 'idle',
            'active_project_id': 'project-1',
        }
    )
    project = {
        '_id': 'project-1',
        'session_id': 'session-1',
        'filename': 'scene.aep',
        'workspace_dir': str(project_root),
        'entry_aep_file': 'scene.aep',
        'aep_files': [],
    }

    async def fake_get_project(session_id: str, project_id: str) -> dict | None:
        assert session_id == 'session-1'
        assert project_id == 'project-1'
        return dict(project)

    async def fake_refresh_project_files(session_id: str, project_id: str) -> dict | None:
        assert session_id == 'session-1'
        assert project_id == 'project-1'
        return dict(project)

    monkeypatch.setattr(module, 'get_session_collection', lambda: session_collection)
    monkeypatch.setattr(module.pm, 'get_project', fake_get_project)
    monkeypatch.setattr(module.pm, 'refresh_project_files', fake_refresh_project_files)
    monkeypatch.setattr(module, '_ensure_python_tool_runtime', fake_python_runtime)

    tools = {tool.name: tool for tool in module.build_shotwright_tools('session-1')}
    result = await tools['run_python_code'].handler(
        SimpleNamespace(
            arguments={
                'script_content': (
                    "from pathlib import Path\n"
                    "import os\n"
                    "Path('analysis.json').write_text('{\"ok\": true}', encoding='utf-8')\n"
                    "print(os.environ['SHOTWRIGHT_PROJECT_ID'])\n"
                ),
                'timeout_seconds': 10,
            }
        )
    )

    assert result.result_type == 'success'
    assert (project_root / 'analysis.json').read_text(encoding='utf-8') == '{"ok": true}'

    payload = json.loads(result.text_result_for_llm)
    assert payload['project_id'] == 'project-1'
    assert payload['work_dir'] == str(project_root.resolve())
    assert payload['stdout'].strip() == 'project-1'
    assert {'relative_path': 'analysis.json', 'size_bytes': 12, 'change_type': 'created'} in payload[
        'created_or_modified_files'
    ]


@pytest.mark.asyncio
async def test_run_python_code_times_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module.pm, 'UPLOAD_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(module.pm, 'EXPORT_DIR', tmp_path / 'exports')
    session_collection = FakeSessionCollection(
        {
            '_id': 'session-1',
            'status': 'idle',
            'active_project_id': None,
        }
    )

    monkeypatch.setattr(module, 'get_session_collection', lambda: session_collection)
    monkeypatch.setattr(module, '_ensure_python_tool_runtime', fake_python_runtime)

    tools = {tool.name: tool for tool in module.build_shotwright_tools('session-1')}
    result = await tools['run_python_code'].handler(
        SimpleNamespace(arguments={'script_content': 'import time; time.sleep(5)', 'timeout_seconds': 1})
    )

    assert result.result_type == 'failure'
    payload = json.loads(result.text_result_for_llm)
    assert payload['timed_out'] is True
    assert payload['exit_code'] == -1


@pytest.mark.asyncio
async def test_run_python_code_rejects_work_dir_outside_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(module.pm, 'UPLOAD_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(module.pm, 'EXPORT_DIR', tmp_path / 'exports')
    session_collection = FakeSessionCollection(
        {
            '_id': 'session-1',
            'status': 'idle',
            'active_project_id': None,
        }
    )

    monkeypatch.setattr(module, 'get_session_collection', lambda: session_collection)
    monkeypatch.setattr(module, '_ensure_python_tool_runtime', fake_python_runtime)

    tools = {tool.name: tool for tool in module.build_shotwright_tools('session-1')}
    result = await tools['run_python_code'].handler(
        SimpleNamespace(
            arguments={
                'script_content': "print('nope')",
                'work_dir': str(tmp_path / 'other-session'),
            }
        )
    )

    assert result.result_type == 'failure'
    assert 'work_dir must stay inside this session workspace' in result.error


@pytest.mark.asyncio
async def test_python_tool_runtime_syncs_requirements_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / 'python-runtime'
    venv_dir = runtime_dir / 'aigc-venv'
    requirements_path = tmp_path / 'requirements-aigc.txt'
    requirements_path.write_text('requests==2.32.0\n', encoding='utf-8')
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == [sys.executable, '-m', 'venv']:
            python_path = venv_dir / ('Scripts' if module.os.name == 'nt' else 'bin') / (
                'python.exe' if module.os.name == 'nt' else 'python'
            )
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text('', encoding='utf-8')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(module.settings, 'python_tool_auto_sync_dependencies', True)
    monkeypatch.setattr(module.settings, 'python_tool_runtime_dir', str(runtime_dir))
    monkeypatch.setattr(module.settings, 'python_tool_venv_dir', str(venv_dir))
    monkeypatch.setattr(module.settings, 'python_tool_requirements', str(requirements_path))
    monkeypatch.setattr(module.settings, 'python_tool_pip_cache_dir', '')
    monkeypatch.setattr(module.settings, 'python_tool_system_site_packages', True)
    monkeypatch.setattr(module.settings, 'python_tool_dependency_sync_timeout_seconds', 60)
    monkeypatch.setattr(module, '_run_python_runtime_command', fake_run)

    python_path, runtime, env_patch, error = await module._ensure_python_tool_runtime()

    assert error is None
    assert python_path == venv_dir / ('Scripts' if module.os.name == 'nt' else 'bin') / (
        'python.exe' if module.os.name == 'nt' else 'python'
    )
    assert runtime['synced'] is True
    assert env_patch['VIRTUAL_ENV'] == str(venv_dir)
    assert any(command[:3] == [sys.executable, '-m', 'venv'] for command in calls)
    assert any('-r' in command and str(requirements_path) in command for command in calls)

    calls.clear()
    _python_path, second_runtime, _env_patch, second_error = await module._ensure_python_tool_runtime()

    assert second_error is None
    assert second_runtime['synced'] is False
    assert calls == []

    requirements_path.write_text('requests==2.32.1\n', encoding='utf-8')
    _python_path, third_runtime, _env_patch, third_error = await module._ensure_python_tool_runtime()

    assert third_error is None
    assert third_runtime['synced'] is True
    assert any('-r' in command and str(requirements_path) in command for command in calls)


@pytest.mark.asyncio
async def test_generate_storyboard_tool_passes_crop_to_reference_media(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_generate_storyboard(session_id: str, **kwargs) -> dict:
        captured['session_id'] = session_id
        captured.update(kwargs)
        return {
            'id': 'storyboard-1',
            'session_id': session_id,
            'filename': 'focus.jpg',
            'file_path': 'C:/data/uploads/session-1/_storyboards/focus.jpg',
            'shared_relative_path': 'session-1/_storyboards/focus.jpg',
            'storyboard_image_path': 'C:/data/uploads/session-1/_storyboards/focus.jpg',
        }

    monkeypatch.setattr(module.rm, 'generate_storyboard', fake_generate_storyboard)
    tools = {tool.name: tool for tool in module.build_shotwright_tools('session-1')}

    result = await tools['generate_storyboard_from_reference_video'].handler(
        SimpleNamespace(
            arguments={
                'reference_video_path': 'session-1/_reference-videos/demo.mp4',
                'crop': '10%,20%,50%,25%',
            }
        )
    )

    assert result.result_type == 'success'
    assert captured['session_id'] == 'session-1'
    assert captured['reference_video_path'] == 'session-1/_reference-videos/demo.mp4'
    assert captured['crop'] == '10%,20%,50%,25%'


@pytest.mark.asyncio
async def test_render_tool_sets_rendered_project_back_to_active(monkeypatch: pytest.MonkeyPatch) -> None:
    session_collection = FakeSessionCollection(
        {
            '_id': 'session-1',
            'container_id': 'container-1',
            'active_project_id': 'older-project',
        }
    )
    project = {
        '_id': 'project-1',
        'session_id': 'session-1',
        'filename': 'scene.aep',
        'workspace_dir': 'C:/data/uploads/session-1/project-1',
        'entry_aep_file': 'scene.aep',
        'aep_files': ['scene.aep'],
        'compositions': [{'name': 'Main'}],
        'composition_catalog_updated_at': '2026-04-22T12:00:00Z',
    }
    call_order: list[str] = []

    async def fake_get_project(session_id: str, project_id: str) -> dict | None:
        assert session_id == 'session-1'
        assert project_id == 'project-1'
        return dict(project)

    async def fake_set_active_project(session_id: str, project_id: str) -> dict:
        call_order.append(f'set_active:{project_id}')
        assert session_id == 'session-1'
        return {'_id': project_id}

    async def fake_render_project(**kwargs) -> dict:
        call_order.append(f"render:{kwargs['project_id']}")
        return {
            'success': True,
            'output_path': 'C:/data/exports/session-1/round1.mp4',
            'stream_id': 'stream-1',
            'aep_path': 'C:/data/uploads/session-1/project-1/scene.aep',
            'work_dir': 'C:/data/exports/session-1/_nexrender_work/job-1',
            'stdout_path': 'C:/data/exports/session-1/_nexrender_work/job-1/stdout.log',
            'stderr_path': 'C:/data/exports/session-1/_nexrender_work/job-1/stderr.log',
        }

    async def fake_refresh_project_files(session_id: str, project_id: str) -> dict:
        assert session_id == 'session-1'
        assert project_id == 'project-1'
        return dict(project)

    async def fake_generate_hls(_output_path: str, _stream_id: str) -> dict:
        return {'success': True, 'playlist_url': '/api/streams/stream-1/index.m3u8'}

    def fake_record_render_output(**kwargs) -> dict:
        assert kwargs['project_id'] == 'project-1'
        return {
            'id': 'render-1',
            'filename': 'round1.mp4',
            'project_id': kwargs['project_id'],
        }

    async def fake_publish_session_updated(_session_id: str) -> None:
        return None

    async def fake_publish_context_refresh(_session_id: str, _reason: str, **_kwargs) -> None:
        return None

    monkeypatch.setattr(module, 'get_session_collection', lambda: session_collection)
    monkeypatch.setattr(module.pm, 'get_project', fake_get_project)
    monkeypatch.setattr(module.pm, 'set_active_project', fake_set_active_project)
    monkeypatch.setattr(module.pm, 'refresh_project_files', fake_refresh_project_files)
    monkeypatch.setattr(module.nr, 'render_project', fake_render_project)
    monkeypatch.setattr(module.nr, 'record_render_output', fake_record_render_output)
    monkeypatch.setattr(module, 'generate_hls', fake_generate_hls)
    monkeypatch.setattr(module, 'publish_session_updated', fake_publish_session_updated)
    monkeypatch.setattr(module, 'publish_context_refresh', fake_publish_context_refresh)

    tools = {tool.name: tool for tool in module.build_shotwright_tools('session-1')}
    result = await tools['render_after_effects_project'].handler(
        SimpleNamespace(arguments={'project_id': 'project-1', 'output_name': 'round1.mp4'})
    )

    assert result.result_type == 'success'
    assert call_order[:2] == ['set_active:project-1', 'render:project-1']
    assert session_collection.doc['active_project_id'] == 'project-1'
    assert session_collection.doc['latest_render_path'] == 'C:/data/exports/session-1/round1.mp4'

    payload = json.loads(result.text_result_for_llm)
    assert payload['project_id'] == 'project-1'
    assert payload['active_project_id'] == 'project-1'
    assert payload['project']['_id'] == 'project-1'
    assert payload['render_output']['id'] == 'render-1'


@pytest.mark.asyncio
async def test_render_tool_returns_detailed_failure_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_collection = FakeSessionCollection(
        {
            '_id': 'session-1',
            'container_id': 'container-1',
            'active_project_id': 'project-1',
        }
    )
    project = {
        '_id': 'project-1',
        'session_id': 'session-1',
        'filename': 'scene.aep',
        'workspace_dir': 'C:/data/uploads/session-1/project-1',
        'entry_aep_file': 'scene.aep',
        'aep_files': ['scene.aep'],
        'compositions': [{'name': 'Main'}],
        'composition_catalog_updated_at': '2026-04-22T12:00:00Z',
    }
    stdout_log = tmp_path / 'nexrender.stdout.log'
    stderr_log = tmp_path / 'nexrender.stderr.log'
    stdout_log.write_text('stdout tail\n> job rendering failed\n', encoding='utf-8')
    stderr_log.write_text('aerender ERROR: Composition Main not found\n', encoding='utf-8')

    async def fake_get_project(session_id: str, project_id: str) -> dict | None:
        assert session_id == 'session-1'
        assert project_id == 'project-1'
        return dict(project)

    async def fake_set_active_project(session_id: str, project_id: str) -> dict:
        assert session_id == 'session-1'
        return {'_id': project_id}

    async def fake_render_project(**kwargs) -> dict:
        assert kwargs['project_id'] == 'project-1'
        return {
            'success': False,
            'exit_code': 1,
            'cli_exit_code': 1,
            'aep_path': 'C:/data/uploads/session-1/project-1/scene.aep',
            'output_path': 'C:/data/exports/session-1/round1.mp4',
            'work_dir': 'C:/data/exports/session-1/_nexrender_work/project-1-abc123',
            'stdout_path': str(stdout_log),
            'stderr_path': str(stderr_log),
            'output': "Error: Couldn't find a result file",
            'patch_persist_result': {
                'success': False,
                'output': 'AssertionError [ERR_ASSERTION]: job must have template.composition defined',
            },
        }

    monkeypatch.setattr(module, 'get_session_collection', lambda: session_collection)
    monkeypatch.setattr(module.pm, 'get_project', fake_get_project)
    monkeypatch.setattr(module.pm, 'set_active_project', fake_set_active_project)
    monkeypatch.setattr(module.nr, 'render_project', fake_render_project)

    tools = {tool.name: tool for tool in module.build_shotwright_tools('session-1')}
    result = await tools['render_after_effects_project'].handler(
        SimpleNamespace(arguments={'project_id': 'project-1', 'composition': 'Main'})
    )

    assert result.result_type == 'failure'
    assert 'After Effects render failed.' in result.error
    assert 'Composition: Main' in result.error
    assert 'aerender ERROR: Composition Main not found' in result.error
    assert 'job must have template.composition defined' in result.error

    payload = json.loads(result.text_result_for_llm)
    assert payload['requested_composition'] == 'Main'
    assert 'failure_details' in payload
