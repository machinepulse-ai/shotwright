# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Sequence

try:
    import psutil  # type: ignore[reportMissingImports]
except ImportError:  # pragma: no cover - runtime dependency inside the worker image.
    psutil = None


READY_FONT_PATH = Path("C:/Windows/Fonts/simsun.ttc")
AE_RENDER_ONLY_MARKERS = (
    Path("C:/Users/ContainerAdministrator/Documents/ae_render_only_node.txt"),
    Path("C:/Users/Public/Documents/Adobe/ae_render_only_node.txt"),
    Path("C:/Users/ContainerAdministrator/Documents/ae_render_only.txt"),
    Path("C:/Users/Public/Documents/Adobe/ae_render_only.txt"),
)
FONT_READINESS_PATH_FRAGMENTS = (
    "/windows/fonts/",
    "/typesupport/cmaps/",
)
NODE_CANDIDATES = (
    Path("C:/Program Files/nodejs/node.exe"),
    Path("node"),
)
NEXRENDER_ENTRYPOINT_CANDIDATES = (
    Path("C:/Users/ContainerAdministrator/AppData/Roaming/npm/node_modules/@nexrender/cli/src/bin.js"),
    Path("C:/Users/Administrator/AppData/Roaming/npm/node_modules/@nexrender/cli/src/bin.js"),
)
NEXRENDER_BINARY_CANDIDATES = (
    Path("C:/Users/ContainerAdministrator/AppData/Roaming/npm/nexrender-cli.cmd"),
    Path("C:/Users/Administrator/AppData/Roaming/npm/nexrender-cli.cmd"),
    Path("nexrender-cli.cmd"),
)


def as_windows_path(value: str | Path) -> str:
    return str(value).replace("\\", "/")


def resolve_executable(candidates: Sequence[str | Path]) -> str | None:
    for candidate in candidates:
        candidate_text = str(candidate)
        candidate_path = Path(candidate_text)
        if candidate_path.is_absolute() and candidate_path.exists():
            return as_windows_path(candidate_path)
        resolved = shutil.which(candidate_text)
        if resolved:
            return resolved
    return None


def build_afterfx_host_command(afterfx_gui: str, *, render_only: bool) -> list[str]:
    command = [afterfx_gui]
    if render_only:
        command.append("-re")
    command.append("-noui")
    return command


def build_direct_jsx_command(afterfx_dispatch: str, wrapper_script: str) -> list[str]:
    return [afterfx_dispatch, "-r", wrapper_script]


def build_nexrender_command(
    job_path: str,
    work_dir: str,
    binary_path: str,
    *,
    node_binary: str | None = None,
    nexrender_entrypoint: str | None = None,
    nexrender_binary: str | None = None,
    skip_render: bool = False,
) -> list[str]:
    if node_binary and nexrender_entrypoint:
        command = [
            node_binary,
            nexrender_entrypoint,
            "-f",
            job_path,
            "-w",
            work_dir,
            "--reuse",
            "--skip-cleanup",
            "--debug",
            "--binary",
            binary_path,
        ]
        if skip_render:
            command.append("--skip-render")
        return command

    if nexrender_binary:
        command = [
            nexrender_binary,
            "-f",
            job_path,
            "-w",
            work_dir,
            "--reuse",
            "--skip-cleanup",
            "--debug",
            "-b",
            binary_path,
        ]
        if skip_render:
            command.append("--skip-render")
        return command

    resolved_node = resolve_executable(NODE_CANDIDATES)
    resolved_entrypoint = next(
        (as_windows_path(candidate) for candidate in NEXRENDER_ENTRYPOINT_CANDIDATES if candidate.exists()),
        None,
    )
    if resolved_node and resolved_entrypoint:
        return build_nexrender_command(
            job_path,
            work_dir,
            binary_path,
            node_binary=resolved_node,
            nexrender_entrypoint=resolved_entrypoint,
            skip_render=skip_render,
        )

    resolved_cli = resolve_executable(NEXRENDER_BINARY_CANDIDATES)
    if resolved_cli:
        return build_nexrender_command(
            job_path,
            work_dir,
            binary_path,
            nexrender_binary=resolved_cli,
            skip_render=skip_render,
        )

    raise FileNotFoundError("nexrender CLI was not found inside the container")


def read_text_tail(path: Path, max_chars: int = 6000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
    except OSError:
        return ""


def summarize_output(parts: Sequence[str]) -> str:
    return "\n".join(part for part in parts if part and part.strip())


def kill_process_tree(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if psutil is None:
        process.kill()
        process.wait(timeout=5)
        return

    try:
        root_process = psutil.Process(process.pid)
    except psutil.Error:
        process.kill()
        process.wait(timeout=5)
        return

    descendants = root_process.children(recursive=True)
    for child in reversed(descendants):
        try:
            child.kill()
        except psutil.Error:
            pass

    try:
        root_process.kill()
    except psutil.Error:
        pass

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def stop_named_processes(names: Sequence[str]) -> None:
    if psutil is None:
        return
    expected = {name.lower() for name in names}
    for process in psutil.process_iter(["name"]):
        try:
            name = process.info.get("name") or ""
        except (psutil.Error, AttributeError):
            continue
        stem = Path(name).stem.lower()
        if stem in expected or name.lower() in expected:
            try:
                process.kill()
            except psutil.Error:
                pass


def wait_for_after_effects_ready(
    process: subprocess.Popen[Any],
    timeout_seconds: int,
    *,
    ready_font: str,
) -> str:
    if psutil is None:
        raise RuntimeError("psutil is required for After Effects font readiness detection")

    deadline = time.monotonic() + max(timeout_seconds, 1)
    ready_font_norm = as_windows_path(ready_font).lower()
    ae_process = psutil.Process(process.pid)
    while True:
        if process.poll() is not None:
            raise RuntimeError(
                f"After Effects exited before initialization with code {process.returncode}"
            )
        if time.monotonic() > deadline:
            kill_process_tree(process)
            raise TimeoutError("timed out waiting for After Effects to initialize")

        try:
            open_files = open_files_for_process_tree(ae_process)
        except psutil.Error:
            time.sleep(1)
            continue

        readiness_marker = find_font_readiness_marker(open_files, ready_font_norm)
        if readiness_marker:
            return readiness_marker
        time.sleep(1)


def start_after_effects(
    afterfx_gui: str,
    timeout_seconds: int,
    *,
    render_only: bool,
    ready_font: str,
    env: dict[str, str],
) -> tuple[subprocess.Popen[Any], str, str, tuple[str, ...]]:
    marker_action = "ensured" if render_only else "cleared"
    render_only_markers = (
        ensure_ae_render_only_markers(afterfx_gui)
        if render_only
        else clear_ae_render_only_markers(afterfx_gui)
    )
    process = subprocess.Popen(
        build_afterfx_host_command(afterfx_gui, render_only=render_only),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    readiness_marker = wait_for_after_effects_ready(process, timeout_seconds, ready_font=ready_font)
    return process, readiness_marker, marker_action, render_only_markers


def build_child_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    project_mapping = {
        "project_id": "SHOTWRIGHT_PROJECT_ID",
        "project_root": "SHOTWRIGHT_PROJECT_ROOT",
        "project_file": "SHOTWRIGHT_PROJECT_FILE",
        "project_name": "SHOTWRIGHT_PROJECT_NAME",
    }
    for field_name, env_name in project_mapping.items():
        value = getattr(args, field_name, None)
        if value:
            env[env_name] = value
    return env


def ensure_parent_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_text_file(path: Path, text: str = "") -> None:
    ensure_parent_directory(path)
    path.write_text(text, encoding="utf-8")


def iter_ae_render_only_markers(afterfx_gui: str) -> tuple[Path, ...]:
    afterfx_path = Path(afterfx_gui)
    support_dir = afterfx_path.parent if afterfx_path.name.lower() == "afterfx.exe" else afterfx_path
    return (*AE_RENDER_ONLY_MARKERS, support_dir / "ae_render_only.txt", support_dir / "ae_render_only_node.txt")


def ensure_ae_render_only_markers(afterfx_gui: str) -> tuple[str, ...]:
    markers = iter_ae_render_only_markers(afterfx_gui)
    for marker in markers:
        ensure_text_file(marker)
    return tuple(as_windows_path(marker) for marker in markers)


def clear_ae_render_only_markers(afterfx_gui: str, *, ignore_errors: bool = False) -> tuple[str, ...]:
    cleared_markers: list[str] = []
    for marker in iter_ae_render_only_markers(afterfx_gui):
        try:
            if not marker.exists():
                continue
            marker.unlink()
            cleared_markers.append(as_windows_path(marker))
        except FileNotFoundError:
            continue
        except OSError:
            if ignore_errors:
                continue
            raise
    return tuple(cleared_markers)


def should_retry_jsx_dispatch(
    *,
    dispatch_failed_to_inject: bool,
    script_started: bool,
    script_success: bool,
    timed_out: bool,
    attempt_number: int,
    max_attempts: int,
) -> bool:
    return (
        dispatch_failed_to_inject
        and not script_started
        and not script_success
        and not timed_out
        and attempt_number < max_attempts
    )


def should_fallback_to_direct_jsx(
    *,
    dispatch_failed_to_inject: bool,
    script_started: bool,
    script_success: bool,
    timed_out: bool,
) -> bool:
    return dispatch_failed_to_inject and not script_started and not script_success and not timed_out


def should_fallback_to_nexrender_script_job(
    *,
    dispatch_failed_to_inject: bool,
    script_started: bool,
    script_success: bool,
    timed_out: bool,
    job_path: str | None,
    work_dir: str | None,
) -> bool:
    return (
        dispatch_failed_to_inject
        and not script_started
        and not script_success
        and not timed_out
        and bool(job_path)
        and bool(work_dir)
    )


def open_files_for_process_tree(process: Any) -> set[str]:
    candidates = [process]
    try:
        candidates.extend(process.children(recursive=True))
    except psutil.Error:
        pass

    open_files: set[str] = set()
    for candidate in candidates:
        try:
            open_files.update(as_windows_path(item.path).lower() for item in candidate.open_files())
        except psutil.Error:
            continue
    return open_files


def find_font_readiness_marker(open_files: set[str], ready_font: str) -> str | None:
    if ready_font in open_files:
        return ready_font

    for item in sorted(open_files):
        if any(fragment in item for fragment in FONT_READINESS_PATH_FRAGMENTS):
            return item
    return None


def find_latest_file(root: Path, pattern: str) -> Path | None:
    try:
        candidates = sorted(root.rglob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return candidates[0] if candidates else None


def run_jsx(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    env = build_child_env(args)
    stdout_log = Path(args.stdout_log)
    stderr_log = Path(args.stderr_log)
    jsx_log = Path(args.jsx_log)
    ensure_parent_directory(stdout_log)
    ensure_parent_directory(stderr_log)
    ensure_parent_directory(jsx_log)
    for path in (stdout_log, stderr_log, jsx_log):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    afterfx_process: subprocess.Popen[Any] | None = None
    dispatch_process: subprocess.Popen[Any] | None = None
    after_effects_ready = False
    after_effects_ready_marker: str | None = None
    render_only_marker_action = "cleared"
    render_only_markers: tuple[str, ...] = ()
    dispatch_retry_count = 0
    max_dispatch_attempts = 2
    overall_deadline = time.monotonic() + max(args.timeout_seconds, 1)
    timed_out = False
    dispatch_failed_to_inject = False
    script_success = False
    script_error = False
    script_started = False
    script_ended = False
    direct_fallback_used = False
    direct_fallback_failed_to_start = False
    nexrender_fallback_used = False
    nexrender_fallback_failed_to_start = False
    nexrender_process: subprocess.Popen[Any] | None = None
    try:
        for attempt_number in range(1, max_dispatch_attempts + 1):
            for path in (stdout_log, stderr_log, jsx_log):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

            stop_named_processes(("AfterFX", "AfterFX.com"))
            remaining_timeout = max(int(overall_deadline - time.monotonic()), 1)
            ready_timeout = min(remaining_timeout, 120)
            afterfx_process, after_effects_ready_marker, render_only_marker_action, render_only_markers = start_after_effects(
                args.afterfx_gui,
                ready_timeout,
                render_only=False,
                ready_font=args.ready_font,
                env=env,
            )
            after_effects_ready = True

            with stdout_log.open("w", encoding="utf-8") as stdout_handle, stderr_log.open(
                "w", encoding="utf-8"
            ) as stderr_handle:
                dispatch_process = subprocess.Popen(
                    [args.afterfx_dispatch, "-r", args.wrapper_script],
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=env,
                )

            probe_deadline = time.monotonic() + min(max(int(overall_deadline - time.monotonic()), 1), 30)
            success_grace_deadline: float | None = None
            error_grace_deadline: float | None = None
            script_started = False
            script_success = False
            script_error = False
            script_ended = False
            timed_out = False
            dispatch_failed_to_inject = False

            while True:
                jsx_log_text = read_text_tail(jsx_log, max_chars=12000)
                if not script_started and "SHOTWRIGHT_JSX_START" in jsx_log_text:
                    script_started = True
                if not script_success and "SHOTWRIGHT_JSX_SUCCESS" in jsx_log_text:
                    script_success = True
                    success_grace_deadline = time.monotonic() + 5
                if not script_error and "SHOTWRIGHT_JSX_ERROR:" in jsx_log_text:
                    script_error = True
                    error_grace_deadline = time.monotonic() + 2
                if not script_ended and "SHOTWRIGHT_JSX_END" in jsx_log_text:
                    script_ended = True

                if script_success and (script_ended or (success_grace_deadline is not None and time.monotonic() >= success_grace_deadline)):
                    break
                if script_error and (script_ended or (error_grace_deadline is not None and time.monotonic() >= error_grace_deadline)):
                    break

                if not script_started and time.monotonic() >= probe_deadline:
                    if dispatch_process.poll() is not None:
                        dispatch_failed_to_inject = True
                        break

                if time.monotonic() >= overall_deadline:
                    timed_out = True
                    break

                if dispatch_process.poll() is not None and not script_started and not script_success:
                    dispatch_failed_to_inject = True
                    break

                time.sleep(1)

            retry_dispatch = should_retry_jsx_dispatch(
                dispatch_failed_to_inject=dispatch_failed_to_inject,
                script_started=script_started,
                script_success=script_success,
                timed_out=timed_out,
                attempt_number=attempt_number,
                max_attempts=max_dispatch_attempts,
            )
            if retry_dispatch:
                dispatch_retry_count += 1
                kill_process_tree(dispatch_process)
                kill_process_tree(afterfx_process)
                dispatch_process = None
                afterfx_process = None
                clear_ae_render_only_markers(args.afterfx_gui, ignore_errors=True)
                stop_named_processes(("AfterFX", "AfterFX.com"))
                continue
            break

        if should_fallback_to_nexrender_script_job(
            dispatch_failed_to_inject=dispatch_failed_to_inject,
            script_started=script_started,
            script_success=script_success,
            timed_out=timed_out,
            job_path=getattr(args, "job_path", None),
            work_dir=getattr(args, "work_dir", None),
        ):
            nexrender_fallback_used = True
            kill_process_tree(dispatch_process)
            kill_process_tree(afterfx_process)
            dispatch_process = None
            afterfx_process = None
            clear_ae_render_only_markers(args.afterfx_gui, ignore_errors=True)
            stop_named_processes(("node", "ffmpeg", "AfterFX", "AfterFX.com"))

            with stdout_log.open("a", encoding="utf-8") as stdout_handle, stderr_log.open(
                "a",
                encoding="utf-8",
            ) as stderr_handle:
                nexrender_process = subprocess.Popen(
                    build_nexrender_command(
                        args.job_path,
                        args.work_dir,
                        args.afterfx_gui,
                        skip_render=True,
                    ),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=env,
                )

            nexrender_probe_deadline = time.monotonic() + min(max(int(overall_deadline - time.monotonic()), 1), 60)
            success_grace_deadline = None
            error_grace_deadline = None
            script_started = False
            script_success = False
            script_error = False
            script_ended = False
            timed_out = False
            dispatch_failed_to_inject = False

            while True:
                jsx_log_text = read_text_tail(jsx_log, max_chars=12000)
                if not script_started and "SHOTWRIGHT_JSX_START" in jsx_log_text:
                    script_started = True
                if not script_success and "SHOTWRIGHT_JSX_SUCCESS" in jsx_log_text:
                    script_success = True
                    success_grace_deadline = time.monotonic() + 5
                if not script_error and "SHOTWRIGHT_JSX_ERROR:" in jsx_log_text:
                    script_error = True
                    error_grace_deadline = time.monotonic() + 2
                if not script_ended and "SHOTWRIGHT_JSX_END" in jsx_log_text:
                    script_ended = True

                if script_success and (script_ended or (success_grace_deadline is not None and time.monotonic() >= success_grace_deadline)):
                    break
                if script_error and (script_ended or (error_grace_deadline is not None and time.monotonic() >= error_grace_deadline)):
                    break

                if not script_started and time.monotonic() >= nexrender_probe_deadline and nexrender_process.poll() is not None:
                    nexrender_fallback_failed_to_start = True
                    break

                if time.monotonic() >= overall_deadline:
                    timed_out = True
                    break

                if nexrender_process.poll() is not None and not script_started and not script_success:
                    nexrender_fallback_failed_to_start = True
                    break

                time.sleep(1)

        elif should_fallback_to_direct_jsx(
            dispatch_failed_to_inject=dispatch_failed_to_inject,
            script_started=script_started,
            script_success=script_success,
            timed_out=timed_out,
        ):
            direct_fallback_used = True
            kill_process_tree(dispatch_process)
            kill_process_tree(afterfx_process)
            dispatch_process = None
            afterfx_process = None
            clear_ae_render_only_markers(args.afterfx_gui, ignore_errors=True)
            stop_named_processes(("AfterFX", "AfterFX.com"))

            with stdout_log.open("a", encoding="utf-8") as stdout_handle, stderr_log.open(
                "a",
                encoding="utf-8",
            ) as stderr_handle:
                dispatch_process = subprocess.Popen(
                    build_direct_jsx_command(args.afterfx_dispatch, args.wrapper_script),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=env,
                )

            direct_probe_deadline = time.monotonic() + min(max(int(overall_deadline - time.monotonic()), 1), 45)
            success_grace_deadline = None
            error_grace_deadline = None
            script_started = False
            script_success = False
            script_error = False
            script_ended = False
            timed_out = False
            dispatch_failed_to_inject = False

            while True:
                jsx_log_text = read_text_tail(jsx_log, max_chars=12000)
                if not script_started and "SHOTWRIGHT_JSX_START" in jsx_log_text:
                    script_started = True
                if not script_success and "SHOTWRIGHT_JSX_SUCCESS" in jsx_log_text:
                    script_success = True
                    success_grace_deadline = time.monotonic() + 5
                if not script_error and "SHOTWRIGHT_JSX_ERROR:" in jsx_log_text:
                    script_error = True
                    error_grace_deadline = time.monotonic() + 2
                if not script_ended and "SHOTWRIGHT_JSX_END" in jsx_log_text:
                    script_ended = True

                if script_success and (script_ended or (success_grace_deadline is not None and time.monotonic() >= success_grace_deadline)):
                    break
                if script_error and (script_ended or (error_grace_deadline is not None and time.monotonic() >= error_grace_deadline)):
                    break

                if not script_started and time.monotonic() >= direct_probe_deadline and dispatch_process.poll() is not None:
                    direct_fallback_failed_to_start = True
                    break

                if time.monotonic() >= overall_deadline:
                    timed_out = True
                    break

                if dispatch_process.poll() is not None and not script_started and not script_success:
                    direct_fallback_failed_to_start = True
                    break

                time.sleep(1)

        output_parts = []
        stdout_tail = read_text_tail(stdout_log)
        stderr_tail = read_text_tail(stderr_log)
        jsx_log_tail = read_text_tail(jsx_log, max_chars=12000)
        if after_effects_ready:
            output_parts.append(
                "Shotwright prewarmed After Effects in JSX mode with -noui and observed the font readiness marker."
            )
        if after_effects_ready_marker:
            output_parts.append(f"Readiness marker: {after_effects_ready_marker}")
        if render_only_markers:
            output_parts.append(
                f"AE render-only markers {render_only_marker_action}: {', '.join(render_only_markers)}"
            )
        if dispatch_retry_count:
            output_parts.append(
                f"Shotwright retried JSX dispatch {dispatch_retry_count} time(s) after the warmed host rejected the initial injection attempt."
            )
        if dispatch_failed_to_inject:
            output_parts.append(
                "Shotwright observed the AfterFX dispatcher exit without starting the JSX wrapper in the warmed host."
            )
        if nexrender_fallback_used:
            output_parts.append(
                "Shotwright fell back to a script-only nexrender job after the warmed dispatcher rejected injection."
            )
        if nexrender_fallback_failed_to_start:
            output_parts.append(
                "Shotwright script-only nexrender fallback exited before the JSX wrapper started."
            )
        if direct_fallback_used:
            output_parts.append(
                "Shotwright fell back to direct AfterFX dispatcher execution after the warmed dispatcher rejected injection."
            )
        if direct_fallback_failed_to_start:
            output_parts.append(
                "Shotwright direct AfterFX dispatcher fallback exited before the JSX wrapper started."
            )
        if timed_out:
            output_parts.append("AfterFX JSX execution timed out.")
        if script_success:
            output_parts.append("Shotwright JSX script reported success.")
        if script_error:
            output_parts.append("Shotwright JSX script reported an error.")
        output_text = summarize_output((stdout_tail, stderr_tail, jsx_log_tail, *output_parts))

        success = script_success and not script_error and not timed_out and not dispatch_failed_to_inject
        result = {
            "command": "jsx",
            "runner": Path(args.afterfx_dispatch).name,
            "after_effects_ready": after_effects_ready,
            "after_effects_ready_marker": after_effects_ready_marker,
            "render_only_marker_action": render_only_marker_action,
            "ensured_markers": list(render_only_markers),
            "dispatch_retry_count": dispatch_retry_count,
            "timed_out": timed_out,
            "dispatch_failed_to_inject": dispatch_failed_to_inject,
            "nexrender_fallback_used": nexrender_fallback_used,
            "nexrender_fallback_failed_to_start": nexrender_fallback_failed_to_start,
            "direct_fallback_used": direct_fallback_used,
            "direct_fallback_failed_to_start": direct_fallback_failed_to_start,
            "success_marker_seen": script_success,
            "error_marker_seen": script_error,
            "success": success,
            "stdout_log": as_windows_path(stdout_log),
            "stderr_log": as_windows_path(stderr_log),
            "jsx_log": as_windows_path(jsx_log),
            "output": output_text,
        }
        if dispatch_process is not None:
            result["dispatch_exit_code"] = dispatch_process.poll()
        if nexrender_fallback_used and nexrender_process is not None:
            result["nexrender_fallback_exit_code"] = nexrender_process.poll()
        if direct_fallback_used and dispatch_process is not None:
            result["direct_dispatch_exit_code"] = dispatch_process.poll()
        exit_code = 0 if success else 124 if timed_out else 127 if dispatch_failed_to_inject else 1
        return exit_code, result
    finally:
        kill_process_tree(nexrender_process)
        kill_process_tree(dispatch_process)
        kill_process_tree(afterfx_process)
        clear_ae_render_only_markers(args.afterfx_gui, ignore_errors=True)
        stop_named_processes(("AfterFX", "AfterFX.com"))


def run_render(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    env = os.environ.copy()
    stdout_log = Path(args.stdout_log)
    stderr_log = Path(args.stderr_log)
    work_dir = Path(args.work_dir)
    output_path = Path(args.output_path)
    ensure_parent_directory(stdout_log)
    ensure_parent_directory(stderr_log)
    stdout_log.unlink(missing_ok=True)
    stderr_log.unlink(missing_ok=True)

    afterfx_process: subprocess.Popen[Any] | None = None
    render_process: subprocess.Popen[Any] | None = None
    ready_timeout = min(max(args.timeout_seconds, 30), 120)
    after_effects_ready = False
    after_effects_ready_marker: str | None = None
    render_only_marker_action = "ensured"
    render_only_markers: tuple[str, ...] = ()
    try:
        stop_named_processes(("node", "ffmpeg", "aerender", "AfterFX", "AfterFX.com"))
        afterfx_process, after_effects_ready_marker, render_only_marker_action, render_only_markers = start_after_effects(
            args.afterfx_gui,
            ready_timeout,
            render_only=True,
            ready_font=args.ready_font,
            env=env,
        )
        after_effects_ready = True

        command = build_nexrender_command(
            args.job_path,
            args.work_dir,
            args.binary_path,
        )
        with stdout_log.open("w", encoding="utf-8") as stdout_handle, stderr_log.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            render_process = subprocess.Popen(
                command,
                stdout=stdout_handle,
                stderr=stderr_handle,
                cwd=args.work_dir,
                env=env,
            )

        deadline = time.monotonic() + max(args.timeout_seconds, 1)
        timed_out = False
        while True:
            if render_process.poll() is not None:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(1)

        if timed_out:
            kill_process_tree(render_process)
            stop_named_processes(("node", "ffmpeg", "aerender", "AfterFX", "AfterFX.com"))

        stdout_tail = read_text_tail(stdout_log)
        stderr_tail = read_text_tail(stderr_log)
        aerender_log = find_latest_file(work_dir, "aerender-*.log")
        aerender_log_tail = read_text_tail(aerender_log) if aerender_log else ""
        render_completed = "Finished composition" in aerender_log_tail
        output_exists = output_path.exists()
        cli_exit_code = render_process.poll() if render_process is not None else None
        success = not timed_out and (cli_exit_code == 0 or (output_exists and render_completed))

        output_parts = []
        if after_effects_ready:
            output_parts.append(
                "Shotwright prewarmed After Effects in render-engine mode with -re -noui and observed the font readiness marker."
            )
        if after_effects_ready_marker:
            output_parts.append(f"Readiness marker: {after_effects_ready_marker}")
        if render_only_markers:
            output_parts.append(
                f"AE render-only markers {render_only_marker_action}: {', '.join(render_only_markers)}"
            )
        if render_completed:
            output_parts.append("Shotwright observed aerender completion in the job log.")
        if timed_out:
            output_parts.append("nexrender render timed out.")

        output_text = summarize_output((stdout_tail, stderr_tail, aerender_log_tail, *output_parts))
        result = {
            "command": "render",
            "after_effects_ready": after_effects_ready,
            "after_effects_ready_marker": after_effects_ready_marker,
            "render_only_marker_action": render_only_marker_action,
            "ensured_markers": list(render_only_markers),
            "timed_out": timed_out,
            "render_completed": render_completed,
            "output_exists": output_exists,
            "success": success,
            "cli_exit_code": cli_exit_code,
            "stdout_log": as_windows_path(stdout_log),
            "stderr_log": as_windows_path(stderr_log),
            "aerender_log": as_windows_path(aerender_log) if aerender_log else None,
            "output": output_text,
            "command_line": command,
        }
        exit_code = 0 if success else 124 if timed_out else (cli_exit_code or 1)
        return exit_code, result
    finally:
        kill_process_tree(render_process)
        kill_process_tree(afterfx_process)
        clear_ae_render_only_markers(args.afterfx_gui, ignore_errors=True)
        stop_named_processes(("node", "ffmpeg", "aerender", "AfterFX", "AfterFX.com"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Warm a reusable AE host and run JSX or nexrender as sibling child processes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    jsx_parser = subparsers.add_parser("jsx")
    jsx_parser.add_argument("--afterfx-gui", required=True)
    jsx_parser.add_argument("--afterfx-dispatch", required=True)
    jsx_parser.add_argument("--wrapper-script", required=True)
    jsx_parser.add_argument("--jsx-log", required=True)
    jsx_parser.add_argument("--stdout-log", required=True)
    jsx_parser.add_argument("--stderr-log", required=True)
    jsx_parser.add_argument("--timeout-seconds", type=int, required=True)
    jsx_parser.add_argument("--ready-font", default=as_windows_path(READY_FONT_PATH))
    jsx_parser.add_argument("--project-id")
    jsx_parser.add_argument("--project-root")
    jsx_parser.add_argument("--project-file")
    jsx_parser.add_argument("--project-name")
    jsx_parser.add_argument("--job-path")
    jsx_parser.add_argument("--work-dir")

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--afterfx-gui", required=True)
    render_parser.add_argument("--job-path", required=True)
    render_parser.add_argument("--work-dir", required=True)
    render_parser.add_argument("--binary-path", required=True)
    render_parser.add_argument("--output-path", required=True)
    render_parser.add_argument("--stdout-log", required=True)
    render_parser.add_argument("--stderr-log", required=True)
    render_parser.add_argument("--timeout-seconds", type=int, required=True)
    render_parser.add_argument("--ready-font", default=as_windows_path(READY_FONT_PATH))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "jsx":
            exit_code, result = run_jsx(args)
        else:
            exit_code, result = run_render(args)
    except Exception as error:  # pragma: no cover - defensive runtime guard.
        result = {
            "command": getattr(args, "command", None),
            "success": False,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "output": summarize_output((str(error), traceback.format_exc())),
        }
        exit_code = 1

    print(json.dumps(result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())