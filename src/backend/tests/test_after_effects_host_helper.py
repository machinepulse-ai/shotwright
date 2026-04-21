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


def test_build_afterfx_host_command_uses_re_and_noui() -> None:
    helper = load_helper_module()

    assert helper.build_afterfx_host_command("C:/Program Files/Adobe/AfterFX.exe") == [
        "C:/Program Files/Adobe/AfterFX.exe",
        "-re",
        "-noui",
    ]


def test_build_nexrender_command_keeps_reuse_for_node_entrypoint() -> None:
    helper = load_helper_module()

    command = helper.build_nexrender_command(
        "C:/data/job.json",
        "C:/data/work",
        "C:/Program Files/Adobe/aerender.exe",
        node_binary="C:/Program Files/nodejs/node.exe",
        nexrender_entrypoint="C:/Users/ContainerAdministrator/AppData/Roaming/npm/node_modules/@nexrender/cli/src/bin.js",
    )

    assert command[:2] == [
        "C:/Program Files/nodejs/node.exe",
        "C:/Users/ContainerAdministrator/AppData/Roaming/npm/node_modules/@nexrender/cli/src/bin.js",
    ]
    assert "--reuse" in command
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