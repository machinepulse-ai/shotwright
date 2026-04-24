from pathlib import Path, PureWindowsPath

import pytest

from app.services import nexrender as module


def test_build_nexrender_script_job_omits_composition_when_unspecified() -> None:
    job = module._build_nexrender_script_job(
        "C:/data/uploads/session/project.aep",
        "C:/data/exports/script.jsx",
    )

    assert job["template"] == {
        "src": "file:///C:/data/uploads/session/project.aep",
        "name": "project.aep",
        "composition": "Main",
        "outputExt": "mp4",
    }
    assert job["assets"] == [
        {
            "type": "script",
            "src": "file:///C:/data/exports/script.jsx",
            "name": "script.jsx",
        }
    ]
    assert job["ae_render"]["script_only"] is True


def test_resolve_patch_script_path_maps_container_workspace_path_to_repo_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    patch_script = repo_root / "circle_round1_patch.jsx"
    patch_script.write_text("// patch\n", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", repo_root)

    resolved = module._resolve_patch_script_path("C:/workspace/circle_round1_patch.jsx")

    assert resolved == patch_script


def test_resolve_patch_script_path_falls_back_to_project_workspace_basename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    project_dir = tmp_path / "uploads" / "session-1" / "project-1"
    project_dir.mkdir(parents=True)
    patch_script = project_dir / "circle_round1_patch.jsx"
    patch_script.write_text("// patch\n", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", repo_root)

    resolved = module._resolve_patch_script_path(
        "C:/workspace/circle_round1_patch.jsx",
        project={"workspace_dir": str(project_dir)},
    )

    assert resolved == patch_script


def test_build_nexrender_script_job_keeps_bootstrap_composition() -> None:
    job = module._build_nexrender_script_job(
        module.CONTAINER_BOOTSTRAP_TEMPLATE,
        "C:/data/exports/wrapper.jsx",
        composition=module.BOOTSTRAP_TEMPLATE_COMPOSITION,
    )

    assert job["template"]["composition"] == "Main"


def test_build_nexrender_job_uses_direct_mp4_copy_pattern() -> None:
    job = module.build_nexrender_job(
        aep_path="C:/data/uploads/session/project.aep",
        composition="Main",
        output_path="C:/data/exports/session/output.mp4",
        patch_script="C:/data/exports/session/patch.jsx",
    )

    assert job["template"] == {
        "src": "file:///C:/data/uploads/session/project.aep",
        "composition": "Main",
        "outputExt": "mp4",
    }
    assert job["actions"]["postrender"] == [
        {
            "module": "@nexrender/action-copy",
            "input": "result.mp4",
            "output": "C:/data/exports/session/output.mp4",
        }
    ]
    assert job["assets"] == [
        {
            "type": "script",
            "src": "file:///C:/data/exports/session/patch.jsx",
        }
    ]


def test_build_nexrender_cli_command_includes_skip_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_resolve_executable", lambda candidates: "C:/tools/nexrender-cli.cmd")

    command = module._build_nexrender_cli_command(
        "C:/data/exports/job.json",
        "C:/data/exports/work",
        "C:/Program Files/Adobe/Adobe After Effects 2026/Support Files/aerender.exe",
        skip_render=True,
    )

    assert command[0] == "C:/tools/nexrender-cli.cmd"
    assert "--skip-render" in command
    assert "--skip-cleanup" in command
    assert command[command.index("-b") + 1].endswith("aerender.exe")


def test_build_jsx_wrapper_embeds_managed_project_path() -> None:
    wrapper = module._build_jsx_wrapper(
        "C:/data/exports/user-script.jsx",
        "C:/data/exports/script.log",
        "C:/data/uploads/session/project/scene.aep",
        "Main",
        None,
        "C:/data/uploads/session/project/.shotwright-project.json",
    )

    assert 'var __shotwrightManagedProjectPath = "c:/data/uploads/session/project/scene.aep";' in wrapper
    assert 'var __shotwrightBootstrapCompName = "Main";' in wrapper
    assert 'var __shotwrightProjectMetadataPath = "C:/data/uploads/session/project/.shotwright-project.json";' in wrapper
    assert "SHOTWRIGHT_BOOTSTRAP_COMP_RENAMED:main->" in wrapper
    assert "SHOTWRIGHT_PROJECT_METADATA_WRITTEN:" in wrapper
    assert "function __shotwrightSerializeJson(value, depth)" in wrapper
    assert "SHOTWRIGHT_PROJECT_METADATA_UNAVAILABLE:JSON" not in wrapper
    assert "$.getenv(\"SHOTWRIGHT_PROJECT_FILE\")" not in wrapper


def test_record_render_output_writes_metadata_and_lists_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    export_root = tmp_path / "exports"
    session_dir = export_root / "session-1"
    session_dir.mkdir(parents=True, exist_ok=True)
    output_path = session_dir / "preview.mp4"
    output_path.write_bytes(b"mp4")
    monkeypatch.setattr(module, "EXPORT_DIR", export_root)

    metadata = module.record_render_output(
        session_id="session-1",
        project_id="project-1",
        output_path=output_path,
        composition="Main",
        aep_path="C:/data/uploads/session-1/project-1/scene.aep",
        work_dir="C:/data/exports/session-1/_nexrender_work/project-1-abc123",
        stdout_path="C:/data/exports/session-1/_nexrender_work/project-1-abc123/nexrender.stdout.log",
        stderr_path="C:/data/exports/session-1/_nexrender_work/project-1-abc123/nexrender.stderr.log",
        stream_id="session-1-project-1-abc123",
        playlist_url="/api/streams/session-1-project-1-abc123/index.m3u8",
        project_workspace_dir="C:/data/uploads/session-1/project-1",
    )

    assert metadata["shared_relative_path"] == "session-1/preview.mp4"
    assert metadata["composition"] == "Main"

    listed = module.list_render_outputs("session-1")
    assert [entry["filename"] for entry in listed] == ["preview.mp4"]
    assert listed[0]["playlist_url"] == "/api/streams/session-1-project-1-abc123/index.m3u8"
    assert module.get_render_output("session-1", metadata["id"])["filename"] == "preview.mp4"


def test_resolve_after_effects_dispatch_binary_prefers_afterfx_com(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_resolve_after_effects_binary", lambda name: Path(f"C:/ae/{name}"))

    assert module._resolve_after_effects_dispatch_binary() == Path("C:/ae/AfterFX.com")


def test_resolve_after_effects_dispatch_binary_falls_back_to_afterfx_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resolve(binary_name: str) -> Path:
        if binary_name == "AfterFX.com":
            raise FileNotFoundError(binary_name)
        return Path(f"C:/ae/{binary_name}")

    monkeypatch.setattr(module, "_resolve_after_effects_binary", fake_resolve)

    assert module._resolve_after_effects_dispatch_binary() == Path("C:/ae/AfterFX.exe")


def test_resolve_project_payload_synthesizes_target_for_empty_workspace() -> None:
    payload = module._resolve_project_payload(
        {
            "_id": "project-1",
            "workspace_dir": "C:/data/uploads/session/project-1",
            "filename": "scene.aep",
            "entry_aep_file": None,
            "aep_files": [],
        }
    )

    assert payload == {
        "project_id": "project-1",
        "workspace_dir": "C:/data/uploads/session/project-1",
        "entry_aep_file": "scene.aep",
        "entry_aep_path": str(Path("C:/data/uploads/session/project-1") / "scene.aep"),
        "project_metadata_path": str(Path("C:/data/uploads/session/project-1") / ".shotwright-project.json"),
    }


def test_resolve_nexrender_bootstrap_template_reads_expected_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bootstrap_template = tmp_path / "validation_motion.aep"
    bootstrap_template.write_bytes(b"aep")
    monkeypatch.setattr(module, "CONTAINER_BOOTSTRAP_TEMPLATE", bootstrap_template)

    assert module._resolve_nexrender_bootstrap_template() == bootstrap_template


@pytest.mark.asyncio
async def test_sync_runtime_helper_scripts_pushes_updated_helper_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    helper_path = tmp_path / "after_effects_host.py"
    helper_path.write_text("print('helper-v1')\n", encoding="utf-8")
    monkeypatch.setattr(module, "LOCAL_AFTER_EFFECTS_HOST_SCRIPT", helper_path)
    monkeypatch.setattr(module, "CONTAINER_AFTER_EFFECTS_HOST_SCRIPT", Path("C:/workspace/scripts/after_effects_host.py"))
    monkeypatch.setattr(module, "_RUNTIME_HELPER_SYNC_DIGESTS", {})

    captured: list[tuple[str, dict[str, str]]] = []

    async def fake_put_text_files_in_container(docker_id: str, files: dict[str, str], *, encoding: str = "utf-8") -> None:
        assert encoding == "utf-8"
        captured.append((docker_id, files))

    monkeypatch.setattr(module, "put_text_files_in_container", fake_put_text_files_in_container)

    await module._sync_runtime_helper_scripts("docker-1")
    await module._sync_runtime_helper_scripts("docker-1")

    assert captured == [
        (
            "docker-1",
            {
                str(Path("C:/workspace/scripts/after_effects_host.py")): "print('helper-v1')\n",
            },
        )
    ]


@pytest.mark.asyncio
async def test_persist_patch_script_to_project_runs_jsx_and_refreshes_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    patch_script = tmp_path / "round2_patch.jsx"
    patch_script.write_text("app.project.save(app.project.file);\n", encoding="utf-8")

    captured: dict[str, object] = {}

    async def fake_run_jsx_script(container_db_id: str, script_content: str, *, project: dict | None = None, timeout_seconds: int = 300) -> dict:
        captured["container_db_id"] = container_db_id
        captured["script_content"] = script_content
        captured["project"] = project
        captured["timeout_seconds"] = timeout_seconds
        return {"success": True, "output": "ok", "success_marker_seen": True}

    async def fake_refresh_project_files(session_id: str, project_id: str) -> dict:
        return {
            "_id": project_id,
            "session_id": session_id,
            "workspace_dir": "C:/data/uploads/session-1/project-1",
            "filename": "scene.aep",
            "entry_aep_file": "scene.aep",
            "aep_files": ["scene.aep"],
        }

    monkeypatch.setattr(module, "run_jsx_script", fake_run_jsx_script)
    monkeypatch.setattr(module.pm, "refresh_project_files", fake_refresh_project_files)

    result = await module.persist_patch_script_to_project(
        container_db_id="container-1",
        project={
            "_id": "project-1",
            "session_id": "session-1",
            "workspace_dir": "C:/data/uploads/session-1/project-1",
            "filename": "scene.aep",
            "entry_aep_file": "scene.aep",
            "aep_files": ["scene.aep"],
        },
        patch_script=str(patch_script),
    )

    assert captured["container_db_id"] == "container-1"
    assert captured["script_content"] == "app.project.save(app.project.file);\n"
    assert result["persistence_confirmed"] is True
    assert PureWindowsPath(result["project"]["entry_aep_path"]).as_posix() == "C:/data/uploads/session-1/project-1/scene.aep"