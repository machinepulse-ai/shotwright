"""nexrender integration — build jobs and invoke nexrender-cli inside containers."""

import json
import logging
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.services import project_manager as pm
from app.services.container_manager import exec_in_container, get_container

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
SETUP_VERSIONS_SCRIPT = REPO_ROOT / "scripts" / "install" / "setup_versions.py"
SETUP_VERSIONS_CONFIG = REPO_ROOT / "setup-versions.yml"
CONTAINER_AFTER_EFFECTS_HOST_SCRIPT = Path("C:/workspace/scripts/after_effects_host.py")
ADOBE_INSTALL_BASE_ROOT = Path("C:/Program Files/Adobe")
EXPORT_DIR = Path(settings.export_dir)
CONTAINER_BOOTSTRAP_TEMPLATE = Path("C:/workspace/validation-data/templates/validation_motion.aep")
BOOTSTRAP_TEMPLATE_COMPOSITION = "Main"
NEXRENDER_BINARY_CANDIDATES = (
    Path("C:/Users/ContainerAdministrator/AppData/Roaming/npm/nexrender-cli.cmd"),
    Path("C:/Users/Administrator/AppData/Roaming/npm/nexrender-cli.cmd"),
    Path("nexrender-cli.cmd"),
)


def _read_text_tail(path: str | Path, max_chars: int = 12000) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-max_chars:]
    except OSError:
        return ""


def _resolve_executable(candidates: tuple[Path, ...]) -> str:
    for candidate in candidates:
        candidate_text = str(candidate)
        if candidate.is_absolute() and candidate.exists():
            return candidate_text
        resolved = shutil.which(candidate_text)
        if resolved:
            return resolved
    raise FileNotFoundError(f"missing executable from candidates: {candidates}")


def _build_jsx_wrapper(
    user_script_path: str | Path,
    log_path: str | Path,
    managed_project_path: str | Path | None = None,
    bootstrap_comp_name: str | None = None,
) -> str:
    normalized_script_path = Path(user_script_path).as_posix()
    normalized_log_path = Path(log_path).as_posix()
    normalized_managed_project_path = (
        Path(managed_project_path).as_posix().lower() if managed_project_path else ""
    )
    normalized_bootstrap_comp_name = bootstrap_comp_name or ""
    return "\n".join(
        [
            "(function () {",
            f'    var __shotwrightScript = new File("{normalized_script_path}");',
            f'    var __shotwrightLogFile = new File("{normalized_log_path}");',
            "    function __shotwrightLog(message) {",
            "        try {",
            "            __shotwrightLogFile.encoding = \"UTF-8\";",
            "            if (!__shotwrightLogFile.open(\"a\")) { return; }",
            "            __shotwrightLogFile.writeln(message);",
            "            __shotwrightLogFile.close();",
            "        } catch (__shotwrightLogError) {}",
            "    }",
            "    function __shotwrightDescribeFile(fileRef) {",
            "        try {",
            "            if (!fileRef) { return \"<none>\"; }",
            "            if (typeof fileRef.fsName !== \"undefined\" && fileRef.fsName) { return fileRef.fsName; }",
            "            if (typeof fileRef.fullName !== \"undefined\" && fileRef.fullName) { return fileRef.fullName; }",
            "            return fileRef.toString();",
            "        } catch (__shotwrightDescribeFileError) {",
            "            return \"<unavailable>\";",
            "        }",
            "    }",
            "    function __shotwrightNormalizePath(fileRef) {",
            "        try {",
            "            var __shotwrightPath = __shotwrightDescribeFile(fileRef);",
            "            return __shotwrightPath ? __shotwrightPath.toString().replace(/\\\\/g, \"/\").toLowerCase() : \"\";",
            "        } catch (__shotwrightNormalizePathError) {",
            "            return \"\";",
            "        }",
            "    }",
            f'    var __shotwrightManagedProjectPath = "{normalized_managed_project_path}";',
            f'    var __shotwrightBootstrapCompName = "{normalized_bootstrap_comp_name}";',
            "    function __shotwrightFindCompByName(name) {",
            "        if (!name || !app.project) { return null; }",
            "        for (var itemIndex = 1; itemIndex <= app.project.items.length; itemIndex += 1) {",
            "            var item = app.project.items[itemIndex];",
            "            if (item instanceof CompItem && item.name === name) {",
            "                return item;",
            "            }",
            "        }",
            "        return null;",
            "    }",
            "    function __shotwrightNormalizeBootstrapComp() {",
            "        if (!__shotwrightBootstrapCompName || !app.project) { return; }",
            "        if (__shotwrightFindCompByName(__shotwrightBootstrapCompName)) { return; }",
            "        var __shotwrightLegacyComp = __shotwrightFindCompByName(\"main\");",
            "        if (!__shotwrightLegacyComp || __shotwrightBootstrapCompName === \"main\") { return; }",
            "        try {",
            "            __shotwrightLegacyComp.name = __shotwrightBootstrapCompName;",
            "            __shotwrightLog(\"SHOTWRIGHT_BOOTSTRAP_COMP_RENAMED:main->\" + __shotwrightBootstrapCompName);",
            "        } catch (__shotwrightNormalizeBootstrapCompError) {",
            "            __shotwrightLog(\"SHOTWRIGHT_BOOTSTRAP_COMP_RENAME_FAILED:\" + __shotwrightNormalizeBootstrapCompError.toString());",
            "        }",
            "    }",
            "    function __shotwrightSaveManagedProject() {",
            "        if (!__shotwrightManagedProjectPath || !app.project || typeof app.project.save !== \"function\") { return; }",
            "        __shotwrightNormalizeBootstrapComp();",
            "        var __shotwrightTargetFile = new File(__shotwrightManagedProjectPath);",
            "        var __shotwrightCurrentProjectPath = app.project.file ? __shotwrightNormalizePath(app.project.file) : \"\";",
            "        __shotwrightLog(\"SHOTWRIGHT_PROJECT_SAVE_START:\" + __shotwrightDescribeFile(__shotwrightTargetFile));",
            "        if (__shotwrightCurrentProjectPath && __shotwrightCurrentProjectPath === __shotwrightManagedProjectPath) {",
            "            app.project.save();",
            "        } else {",
            "            app.project.save(__shotwrightTargetFile);",
            "        }",
            "        __shotwrightLog(\"SHOTWRIGHT_PROJECT_SAVE_DONE:\" + __shotwrightDescribeFile(__shotwrightTargetFile));",
            "    }",
            "    __shotwrightLog(\"SHOTWRIGHT_JSX_START\");",
            "    try {",
            "        $.evalFile(__shotwrightScript);",
            "        __shotwrightSaveManagedProject();",
            "        __shotwrightLog(\"SHOTWRIGHT_JSX_SUCCESS\");",
            "    } catch (error) {",
            "        __shotwrightLog(\"SHOTWRIGHT_JSX_ERROR:\" + error.toString());",
            "        if (typeof error.line !== \"undefined\") {",
            "            __shotwrightLog(\"SHOTWRIGHT_JSX_ERROR_LINE:\" + error.line);",
            "        }",
            "        throw error;",
            "    } finally {",
            "        __shotwrightLog(\"SHOTWRIGHT_JSX_END\");",
            "    }",
            "}());",
        ]
    )


def _to_file_uri(path: str | Path) -> str:
    raw_path = str(path).strip()
    if raw_path.lower().startswith("file://"):
        return raw_path
    normalized = Path(raw_path).as_posix().lstrip("/")
    return f"file:///{normalized}"


def _find_latest_rendered_mp4(root: Path) -> Path | None:
    candidates = sorted(
        root.rglob("result.mp4"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _find_latest_after_effects_install_root() -> Path | None:
    if not ADOBE_INSTALL_BASE_ROOT.exists():
        return None

    candidates = sorted(
        (path for path in ADOBE_INSTALL_BASE_ROOT.glob("Adobe After Effects *") if path.is_dir()),
        key=lambda candidate: candidate.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


@lru_cache(maxsize=1)
def _resolve_after_effects_install_root() -> Path:
    import os

    configured_root = os.environ.get("SHOTWRIGHT_INSTALL_ROOT", "").strip()
    if configured_root:
        candidate = Path(configured_root)
        if candidate.exists():
            return candidate

    if SETUP_VERSIONS_SCRIPT.exists() and SETUP_VERSIONS_CONFIG.exists():
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SETUP_VERSIONS_SCRIPT),
                    "--config",
                    str(SETUP_VERSIONS_CONFIG),
                    "--field",
                    "install_root",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            candidate = Path(result.stdout.strip())
            if candidate.exists():
                return candidate
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Failed to resolve After Effects install root from setup-versions.yml: %s", exc)

    latest_install_root = _find_latest_after_effects_install_root()
    if latest_install_root:
        return latest_install_root

    raise FileNotFoundError("After Effects install root could not be resolved inside the runtime container.")


def _resolve_after_effects_binary(binary_name: str) -> Path:
    binary_path = _resolve_after_effects_install_root() / "Support Files" / binary_name
    if not binary_path.exists():
        raise FileNotFoundError(f"After Effects binary not found: {binary_path}")
    return binary_path


def _resolve_after_effects_dispatch_binary() -> Path:
    try:
        return _resolve_after_effects_binary("AfterFX.com")
    except FileNotFoundError:
        return _resolve_after_effects_binary("AfterFX.exe")


def _build_after_effects_host_command(command: str, **kwargs: str | int | None) -> list[str]:
    cmd = ["python", str(CONTAINER_AFTER_EFFECTS_HOST_SCRIPT), command]
    for key, value in kwargs.items():
        if value is None:
            continue
        option = f"--{key.replace('_', '-')}"
        cmd.extend([option, str(value)])
    return cmd


def _parse_after_effects_host_result(raw_output: str) -> dict:
    stripped_output = raw_output.strip()
    if not stripped_output:
        return {}
    try:
        return json.loads(stripped_output)
    except json.JSONDecodeError:
        return {"output": stripped_output}


def _resolve_nexrender_bootstrap_template() -> Path:
    if CONTAINER_BOOTSTRAP_TEMPLATE.exists():
        return CONTAINER_BOOTSTRAP_TEMPLATE
    raise FileNotFoundError(
        "Bootstrap nexrender template is missing. Rebuild the Shotwright images so validation_motion.aep is copied into C:/workspace/validation-data/templates/."
    )


def _build_nexrender_script_job(
    template_src: str | Path,
    script_src: str | Path,
    *,
    composition: str | None = None,
) -> dict:
    template_path = Path(template_src)
    script_path = Path(script_src)
    template_payload: dict[str, str] = {
        "src": _to_file_uri(template_path),
        "name": template_path.name,
        "outputExt": "mp4",
    }
    if composition:
        template_payload["composition"] = composition
    return {
        "template": template_payload,
        "assets": [
            {
                "type": "script",
                "src": _to_file_uri(script_path),
                "name": script_path.name,
            }
        ],
        "actions": {},
        "ae_render": {
            "legacy": False,
            "script_only": True,
        },
    }


def _build_nexrender_cli_command(
    job_path: str | Path,
    work_dir: str | Path,
    binary_path: str | Path,
    *,
    skip_render: bool = False,
) -> list[str]:
    command = [
        _resolve_executable(NEXRENDER_BINARY_CANDIDATES),
        "-f",
        str(job_path),
        "-w",
        str(work_dir),
        "-b",
        str(binary_path),
        "--skip-cleanup",
        "--debug",
    ]
    if skip_render:
        command.append("--skip-render")
    return command


def _resolve_project_payload(project: dict | None) -> dict[str, str] | None:
    if not project:
        return None

    entry_aep_file = project.get("entry_aep_file") or (project.get("aep_files") or [None])[0]
    if not entry_aep_file:
        return {
            "project_id": project["_id"],
            "workspace_dir": project["workspace_dir"],
            "entry_aep_file": project.get("entry_aep_file") or project.get("filename") or "project.aep",
            "entry_aep_path": str(Path(project["workspace_dir"]) / (project.get("entry_aep_file") or project.get("filename") or "project.aep")),
        }

    return {
        "project_id": project["_id"],
        "workspace_dir": project["workspace_dir"],
        "entry_aep_file": entry_aep_file,
        "entry_aep_path": str(Path(project["workspace_dir"]) / entry_aep_file),
    }


def build_nexrender_job(
    aep_path: str,
    composition: str = "Main",
    output_path: str = "C:\\data\\output\\result.mp4",
    patch_script: str | None = None,
) -> dict:
    """Build a nexrender job JSON payload."""
    job: dict = {
        "template": {
            "src": _to_file_uri(aep_path),
            "composition": composition,
            "outputExt": "mp4",
        },
        "assets": [],
        "actions": {
            "postrender": [
                {
                    "module": "@nexrender/action-copy",
                    "input": "result.mp4",
                    "output": output_path,
                },
            ]
        },
    }
    if patch_script:
        job["assets"].append(
            {
                "type": "script",
                "src": _to_file_uri(patch_script),
            }
        )
    return job


async def run_render(
    container_db_id: str,
    *,
    job_path: Path,
    work_dir: Path,
    binary_path: Path,
    expected_output_path: Path,
    timeout_seconds: int = 600,
) -> dict:
    """Execute a nexrender render inside the container."""
    container = await get_container(container_db_id)
    if not container:
        raise ValueError("Container not found")

    stdout_path = work_dir / "nexrender.stdout.log"
    stderr_path = work_dir / "nexrender.stderr.log"
    afterfx_gui_path = _resolve_after_effects_binary("AfterFX.exe")
    cmd = _build_after_effects_host_command(
        "render",
        afterfx_gui=afterfx_gui_path,
        job_path=job_path,
        work_dir=work_dir,
        binary_path=binary_path,
        output_path=expected_output_path,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        timeout_seconds=max(120, timeout_seconds),
    )

    exit_code, raw_output = await exec_in_container(container["docker_id"], cmd)
    helper_result = _parse_after_effects_host_result(raw_output)

    output_exists = expected_output_path.exists()
    if not output_exists:
        fallback_result = _find_latest_rendered_mp4(work_dir)
        if fallback_result:
            expected_output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fallback_result, expected_output_path)
            output_exists = True

    render_completed = bool(helper_result.get("render_completed")) or output_exists

    success = bool(helper_result.get("success")) or output_exists

    combined_output = helper_result.get("output") or raw_output

    return {
        "exit_code": exit_code,
        "cli_exit_code": helper_result.get("cli_exit_code"),
        "success": success,
        "timed_out": bool(helper_result.get("timed_out")) or exit_code == 124,
        "forced_cleanup": False,
        "success_marker_seen": success,
        "render_completed": render_completed,
        "job_path": str(job_path),
        "work_dir": str(work_dir),
        "binary_path": str(binary_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "output_exists": output_exists,
        "output": combined_output[-2000:] if len(combined_output) > 2000 else combined_output,
    }


async def render_project(
    session_id: str,
    project_id: str,
    container_db_id: str,
    aep_relative_path: str | None = None,
    composition: str = "Main",
    output_name: str | None = None,
    patch_script: str | None = None,
    timeout_seconds: int = 600,
) -> dict:
    """Render a managed or uploaded project from the shared Shotwright workspace."""
    project = await pm.get_project(session_id, project_id)
    if not project:
        raise ValueError("Project not found")

    aep_file = aep_relative_path or project.get("entry_aep_file") or (project.get("aep_files") or [None])[0]
    if not aep_file:
        raise ValueError("No .aep file found in uploaded project")

    output_dir = EXPORT_DIR / session_id
    output_dir.mkdir(parents=True, exist_ok=True)

    resolved_output_name = output_name or f"{project_id}-{uuid4().hex[:8]}.mp4"
    output_path = output_dir / resolved_output_name
    aep_path = Path(project["workspace_dir"]) / aep_file
    if not aep_path.exists():
        raise ValueError(f"AEP file not found at {aep_path}")

    work_dir = output_dir / "_nexrender_work" / f"{project_id}-{uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    job_path = work_dir / "job.json"
    aerender_path = _resolve_after_effects_binary("aerender.exe")

    job = build_nexrender_job(
        aep_path=str(aep_path),
        composition=composition,
        output_path=str(output_path),
        patch_script=patch_script,
    )
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

    result = await run_render(
        container_db_id,
        job_path=job_path,
        work_dir=work_dir,
        binary_path=aerender_path,
        expected_output_path=output_path,
        timeout_seconds=timeout_seconds,
    )
    return {
        **result,
        "project_id": project_id,
        "aep_path": str(aep_path),
        "output_path": str(output_path),
        "stream_id": f"{session_id}-{project_id}-{uuid4().hex[:6]}",
    }


async def run_jsx_script(
    container_db_id: str,
    script_content: str,
    *,
    project: dict | None = None,
    timeout_seconds: int = 300,
) -> dict:
    """Write and execute a JSX script via nexrender-cli in the container."""
    container = await get_container(container_db_id)
    if not container:
        raise ValueError("Container not found")

    script_token = uuid4().hex[:8]
    session_scope = project["session_id"] if project and project.get("session_id") else "scratch"
    work_dir = EXPORT_DIR / session_scope / "_nexrender_jsx" / script_token
    work_dir.mkdir(parents=True, exist_ok=True)

    user_script_path = work_dir / "user-script.jsx"
    wrapper_script_path = work_dir / "wrapper-script.jsx"
    job_path = work_dir / "job.json"
    jsx_log_path = work_dir / "script.log"
    stdout_path = work_dir / "nexrender.stdout.log"
    stderr_path = work_dir / "nexrender.stderr.log"
    user_script_path.write_text(script_content, encoding="utf-8")

    project_payload = _resolve_project_payload(project)
    bootstrap_project_path = _resolve_nexrender_bootstrap_template()
    template_path = (
        Path(project_payload["entry_aep_path"])
        if project_payload and Path(project_payload["entry_aep_path"]).exists()
        else bootstrap_project_path
    )
    template_composition = BOOTSTRAP_TEMPLATE_COMPOSITION if template_path == bootstrap_project_path else None

    wrapper_script_path.write_text(
        _build_jsx_wrapper(
            user_script_path,
            jsx_log_path,
            project_payload["entry_aep_path"] if project_payload else None,
            BOOTSTRAP_TEMPLATE_COMPOSITION if template_path == bootstrap_project_path else None,
        ),
        encoding="utf-8",
    )
    job_path.write_text(
        json.dumps(
            _build_nexrender_script_job(
                template_path,
                wrapper_script_path,
                composition=template_composition,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )

    aerender_path = _resolve_after_effects_binary("aerender.exe")
    run_cmd = _build_nexrender_cli_command(
        job_path,
        work_dir,
        aerender_path,
        skip_render=True,
    )
    exit_code, raw_output = await exec_in_container(container["docker_id"], run_cmd)

    stdout_path.write_text(raw_output, encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    jsx_log_text = _read_text_tail(jsx_log_path)
    success_marker_seen = "SHOTWRIGHT_JSX_SUCCESS" in jsx_log_text
    error_marker_seen = "SHOTWRIGHT_JSX_ERROR:" in jsx_log_text
    managed_project_exists = bool(
        project_payload and Path(project_payload["entry_aep_path"]).exists()
    )
    success = success_marker_seen and not error_marker_seen and (exit_code == 0 or managed_project_exists)
    combined_output = "\n".join(
        part
        for part in (
            raw_output.strip(),
            jsx_log_text.strip(),
            f"Template project: {template_path}",
            f"Bootstrap template used: {template_path == bootstrap_project_path}",
        )
        if part
    )

    return {
        "exit_code": exit_code,
        "runner": Path(run_cmd[0]).name,
        "script_path": str(wrapper_script_path),
        "user_script_path": str(user_script_path),
        "jsx_log_path": str(jsx_log_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "job_path": str(job_path),
        "work_dir": str(work_dir),
        "template_path": str(template_path),
        "bootstrap_template_path": str(bootstrap_project_path),
        "project": project_payload,
        "success": success,
        "timed_out": exit_code == 124,
        "forced_cleanup": False,
        "success_marker_seen": success_marker_seen,
        "error_marker_seen": error_marker_seen,
        "output": combined_output[-2000:] if len(combined_output) > 2000 else combined_output,
    }
