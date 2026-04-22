import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER_PATH = REPO_ROOT / "scripts" / "after_effects_host.py"


def load_helper_module():
    spec = importlib.util.spec_from_file_location("shotwright_after_effects_host", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_afterfx_host_command_uses_render_engine_mode_for_renders() -> None:
    helper = load_helper_module()

    assert helper.build_afterfx_host_command(
        "C:/Program Files/Adobe/AfterFX.exe",
        render_only=True,
    ) == [
        "C:/Program Files/Adobe/AfterFX.exe",
        "-re",
        "-noui",
    ]


def test_build_afterfx_host_command_uses_noui_only_for_jsx() -> None:
    helper = load_helper_module()

    assert helper.build_afterfx_host_command(
        "C:/Program Files/Adobe/AfterFX.exe",
        render_only=False,
    ) == [
        "C:/Program Files/Adobe/AfterFX.exe",
        "-noui",
    ]


def test_build_direct_jsx_command_runs_wrapper_through_dispatch_binary() -> None:
    helper = load_helper_module()

    assert helper.build_direct_jsx_command(
        "C:/Program Files/Adobe/AfterFX.exe",
        "C:/data/wrapper.jsx",
    ) == [
        "C:/Program Files/Adobe/AfterFX.exe",
        "-r",
        "C:/data/wrapper.jsx",
    ]


def test_build_nexrender_command_keeps_reuse_for_node_entrypoint() -> None:
    helper = load_helper_module()

    command = helper.build_nexrender_command(
        "C:/data/job.json",
        "C:/data/work",
        "C:/Program Files/Adobe/aerender.exe",
        node_binary="C:/Program Files/nodejs/node.exe",
        nexrender_entrypoint="C:/Users/ContainerAdministrator/AppData/Roaming/npm/node_modules/@nexrender/cli/src/bin.js",
        skip_render=True,
    )

    assert command[:2] == [
        "C:/Program Files/nodejs/node.exe",
        "C:/Users/ContainerAdministrator/AppData/Roaming/npm/node_modules/@nexrender/cli/src/bin.js",
    ]
    assert "--reuse" in command
    assert "--skip-render" in command
    assert "--binary" in command


def test_build_nexrender_command_keeps_reuse_for_cli_fallback() -> None:
    helper = load_helper_module()

    command = helper.build_nexrender_command(
        "C:/data/job.json",
        "C:/data/work",
        "C:/Program Files/Adobe/aerender.exe",
        nexrender_binary="C:/Users/ContainerAdministrator/AppData/Roaming/npm/nexrender-cli.cmd",
    )

    assert command[0] == "C:/Users/ContainerAdministrator/AppData/Roaming/npm/nexrender-cli.cmd"
    assert "--reuse" in command
    assert "-b" in command


def test_iter_ae_render_only_markers_matches_runtime_layout() -> None:
    helper = load_helper_module()

    markers = helper.iter_ae_render_only_markers(
        "C:/Program Files/Adobe/Adobe After Effects 2026/Support Files/AfterFX.exe"
    )

    assert Path("C:/Users/ContainerAdministrator/Documents/ae_render_only_node.txt") in markers
    assert Path("C:/Users/Public/Documents/Adobe/ae_render_only.txt") in markers
    assert Path(
        "C:/Program Files/Adobe/Adobe After Effects 2026/Support Files/ae_render_only.txt"
    ) in markers
    assert Path(
        "C:/Program Files/Adobe/Adobe After Effects 2026/Support Files/ae_render_only_node.txt"
    ) in markers


def test_clear_ae_render_only_markers_removes_existing_files(tmp_path, monkeypatch) -> None:
    helper = load_helper_module()
    first_marker = tmp_path / "ae_render_only.txt"
    second_marker = tmp_path / "ae_render_only_node.txt"
    first_marker.write_text("", encoding="utf-8")
    second_marker.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        helper,
        "iter_ae_render_only_markers",
        lambda afterfx_gui: (first_marker, second_marker, tmp_path / "missing.txt"),
    )

    cleared = helper.clear_ae_render_only_markers("C:/Program Files/Adobe/AfterFX.exe")

    assert cleared == (
        helper.as_windows_path(first_marker),
        helper.as_windows_path(second_marker),
    )
    assert not first_marker.exists()
    assert not second_marker.exists()


def test_should_retry_jsx_dispatch_only_for_pre_wrapper_injection_failures() -> None:
    helper = load_helper_module()

    assert helper.should_retry_jsx_dispatch(
        dispatch_failed_to_inject=True,
        script_started=False,
        script_success=False,
        timed_out=False,
        attempt_number=1,
        max_attempts=2,
    ) is True
    assert helper.should_retry_jsx_dispatch(
        dispatch_failed_to_inject=True,
        script_started=True,
        script_success=False,
        timed_out=False,
        attempt_number=1,
        max_attempts=2,
    ) is False
    assert helper.should_retry_jsx_dispatch(
        dispatch_failed_to_inject=True,
        script_started=False,
        script_success=False,
        timed_out=False,
        attempt_number=2,
        max_attempts=2,
    ) is False


def test_should_fallback_to_direct_jsx_only_after_dispatch_rejection() -> None:
    helper = load_helper_module()

    assert helper.should_fallback_to_direct_jsx(
        dispatch_failed_to_inject=True,
        script_started=False,
        script_success=False,
        timed_out=False,
    ) is True
    assert helper.should_fallback_to_direct_jsx(
        dispatch_failed_to_inject=True,
        script_started=True,
        script_success=False,
        timed_out=False,
    ) is False
    assert helper.should_fallback_to_direct_jsx(
        dispatch_failed_to_inject=True,
        script_started=False,
        script_success=True,
        timed_out=False,
    ) is False
    assert helper.should_fallback_to_direct_jsx(
        dispatch_failed_to_inject=True,
        script_started=False,
        script_success=False,
        timed_out=True,
    ) is False


def test_should_fallback_to_nexrender_script_job_requires_job_context() -> None:
    helper = load_helper_module()

    assert helper.should_fallback_to_nexrender_script_job(
        dispatch_failed_to_inject=True,
        script_started=False,
        script_success=False,
        timed_out=False,
        job_path="C:/data/exports/job.json",
        work_dir="C:/data/exports/work",
    ) is True
    assert helper.should_fallback_to_nexrender_script_job(
        dispatch_failed_to_inject=True,
        script_started=False,
        script_success=False,
        timed_out=False,
        job_path=None,
        work_dir="C:/data/exports/work",
    ) is False
    assert helper.should_fallback_to_nexrender_script_job(
        dispatch_failed_to_inject=True,
        script_started=True,
        script_success=False,
        timed_out=False,
        job_path="C:/data/exports/job.json",
        work_dir="C:/data/exports/work",
    ) is False


def test_find_font_readiness_marker_accepts_simsun_and_font_subsystem_files() -> None:
    helper = load_helper_module()

    simsun = "c:/windows/fonts/simsun.ttc"
    assert helper.find_font_readiness_marker({simsun}, simsun) == simsun
    assert (
        helper.find_font_readiness_marker(
            {
                "c:/program files/adobe/adobe after effects 2026/support files/typesupport/cmaps/wp-symbol"
            },
            simsun,
        )
        == "c:/program files/adobe/adobe after effects 2026/support files/typesupport/cmaps/wp-symbol"
    )