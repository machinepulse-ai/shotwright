from pathlib import Path

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
    )

    assert 'var __shotwrightManagedProjectPath = "c:/data/uploads/session/project/scene.aep";' in wrapper
    assert 'var __shotwrightBootstrapCompName = "Main";' in wrapper
    assert "SHOTWRIGHT_BOOTSTRAP_COMP_RENAMED:main->" in wrapper
    assert "$.getenv(\"SHOTWRIGHT_PROJECT_FILE\")" not in wrapper


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
    }


def test_resolve_nexrender_bootstrap_template_reads_expected_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bootstrap_template = tmp_path / "validation_motion.aep"
    bootstrap_template.write_bytes(b"aep")
    monkeypatch.setattr(module, "CONTAINER_BOOTSTRAP_TEMPLATE", bootstrap_template)

    assert module._resolve_nexrender_bootstrap_template() == bootstrap_template