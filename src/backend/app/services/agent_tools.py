"""Custom Copilot tools exposing Shotwright container and project controls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from copilot.tools import Tool, ToolInvocation, ToolResult

from app.config import settings
from app.database import get_admin_collection, get_message_collection, get_session_collection
from app.services import container_manager as cm
from app.services import nexrender as nr
from app.services import project_manager as pm
from app.services import reference_media as rm
from app.services import tts as tts_media
from app.services.codex_config import resolve_openai_api_key
from app.services.session_streams import publish_context_refresh, publish_session_updated
from app.services.video_streaming import generate_hls

_REFERENCE_ASSET_DIRECTORY = Path("assets") / "references"
_AUDIO_ASSET_DIRECTORY = Path("assets") / "audio"
_PYTHON_TOOL_SCRIPT_DIRECTORY = Path(".shotwright-python")
_PYTHON_TOOL_OUTPUT_LIMIT = 24_000
_PYTHON_TOOL_FILE_LIST_LIMIT = 80
_PYTHON_TOOL_SNAPSHOT_LIMIT = 5_000
_PYTHON_TOOL_ENV_STATE_FILENAME = ".shotwright-python-runtime.json"
_PYTHON_TOOL_DEFAULT_REQUIREMENTS_NAME = "requirements-aigc.txt"
_PYTHON_TOOL_ENV_LOCK = threading.Lock()
_AFTER_EFFECTS_SCRIPT_EXTENSIONS = {".js", ".jsx", ".jsxinc"}
_SENSITIVE_ENV_NAMES = (
    "GITHUB_TOKEN",
    "SHOTWRIGHT_GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "SHOTWRIGHT_OPENAI_API_KEY",
    "SHOTWRIGHT_TTS_OPENAI_API_KEY",
    "SHOTWRIGHT_TTS_AZURE_SPEECH_KEY",
    "SHOTWRIGHT_TTS_ELEVENLABS_API_KEY",
    "AZURE_SPEECH_KEY",
    "SPEECH_KEY",
    "ELEVENLABS_API_KEY",
    "ANTHROPIC_API_KEY",
)
_PYTHON_REQUIREMENT_IMPORT_NAMES = {
    "edge-tts": "edge_tts",
    "faster-whisper": "faster_whisper",
    "imageio-ffmpeg": "imageio_ffmpeg",
    "opencv-contrib-python": "cv2",
    "opencv-contrib-python-headless": "cv2",
    "opencv-python": "cv2",
    "opencv-python-headless": "cv2",
    "openai-whisper": "whisper",
    "pillow": "PIL",
    "python-dotenv": "dotenv",
    "pyyaml": "yaml",
    "scikit-image": "skimage",
    "scikit-learn": "sklearn",
}
_PYTHON_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
SHOTWRIGHT_RECOMMENDED_FONTS = {
    "default_chinese_caption": {
        "postscript_names": [
            "NotoSansSC-Bold",
            "NotoSansSC-Medium",
            "NotoSansSC-Regular",
            "MicrosoftYaHei-Bold",
            "MicrosoftYaHeiUI-Bold",
            "SimHei",
            "SimSun",
        ],
        "notes": "Use for Simplified Chinese subtitles and body text. Prefer PostScript names, not UI display names.",
    },
    "cute_handwritten_chinese": {
        "postscript_names": [
            "LXGWWenKai-Medium",
            "LXGWWenKai-Regular",
            "NotoSansSC-Bold",
        ],
        "notes": "Use for cute pet stickers, friendly captions, and informal Chinese labels.",
    },
    "serif_chinese_title": {
        "postscript_names": [
            "NotoSerifSC-Bold",
            "NotoSerifSC-Medium",
            "NotoSerifSC-Regular",
            "SimSun",
        ],
        "notes": "Use for editorial title cards or premium Chinese title treatment.",
    },
    "avoid_for_chinese": [
        "MS-Gothic",
        "MSGothic",
        "YuGothic",
    ],
    "license": "Bundled recommended families are SIL Open Font License 1.1 fonts installed by scripts/install/install_open_fonts.ps1.",
}
SHOTWRIGHT_CREATIVE_QUALITY_POLICY = {
    "intent_before_execution": [
        "Choose an audience, emotion, story beat, and visual idea before writing JSX; the style should explain the content, not decorate it generically.",
        "For reference-driven work, let the source footage decide rhythm, framing, and text placement before adding effects.",
        "Name the creative reason for each major visual system: typography, motion, color, transitions, stickers, cards, or sound cues.",
    ],
    "avoid_low_effort_success": [
        "A render that is technically valid but visually plain, muddy, generic, or hard to read is not finished.",
        "Do not satisfy broad style words by scattering random transitions, stickers, or text effects; each element must support the story beat.",
        "Do not use stale QA, placeholder comps, or whole-frame pixel ratios as proof of creative quality.",
    ],
    "storyboard_self_review": [
        "After rendering, inspect full-frame and focused storyboards and identify the weakest frame before finalizing.",
        "Ask whether the result has a clear first impression on a phone-sized screen, a coherent visual hierarchy, readable text, and at least one deliberate memorable moment.",
        "If the storyboard only proves that nothing is broken, revise the design instead of finalizing.",
    ],
}

SHOTWRIGHT_SUBTITLE_STYLE_POLICY = {
    "principles": [
        "Do not force a universal subtitle look; choose typography, backing, contrast, and motion from the current art direction and footage.",
        "Subtitle design is part of the creative idea. It should feel intentional in the storyboard, not merely pass a readability checklist.",
        "Dense or busy footage needs an explicit separation strategy, but the strategy can be any style that fits the piece.",
    ],
    "quality_risks": [
        "Heavy dark strokes or shadows that become the visible style instead of a readability aid.",
        "Text placed directly over busy footage without a deliberate contrast or layout reason.",
        "Style labels such as cute, premium, TVC, or Douyin that are claimed in the response but not visible in storyboard frames.",
        "Repeating a black-dominant caption treatment after the user rejects black subtitles.",
    ],
    "qa_checks": [
        "Inspect actual text layer fill, stroke, shadow, and backing-shape colors in JSX or AE properties before rendering.",
        "Review cropped subtitle-zone storyboard frames at a large enough size; do not rely on whole-frame black-pixel ratios.",
        "If captions are technically legible but visually weak, muddy, generic, or disconnected from the footage, revise before finalizing.",
    ],
}


def _tool_success(payload: dict, session_log: str) -> ToolResult:
    return ToolResult(
        text_result_for_llm=_serialize_tool_payload(payload),
        result_type="success",
        session_log=session_log,
    )


def _tool_failure(message: str, *, error: str | None = None) -> ToolResult:
    return ToolResult(
        text_result_for_llm=message,
        result_type="failure",
        error=error or message,
    )


def _normalize_tool_payload_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize_tool_payload_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_tool_payload_value(item) for item in value]
    return str(value)


def _serialize_tool_payload(payload: dict) -> str:
    return json.dumps(_normalize_tool_payload_value(payload), ensure_ascii=False)


def _truncate_text(value: str, limit: int = _PYTHON_TOOL_OUTPUT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}\n... <truncated {omitted} chars>"


def _redact_sensitive_text(value: str) -> str:
    redacted = value
    for env_name in _SENSITIVE_ENV_NAMES:
        secret = os.environ.get(env_name)
        if secret and len(secret) >= 6:
            redacted = redacted.replace(secret, f"<redacted:{env_name}>")
    return redacted


def _path_is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _snapshot_workspace_files(root: Path, *, limit: int = _PYTHON_TOOL_SNAPSHOT_LIMIT) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}

    snapshot: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative_path = path.relative_to(root).as_posix()
            stat = path.stat()
        except OSError:
            continue
        snapshot[relative_path] = (stat.st_size, stat.st_mtime_ns)
        if len(snapshot) >= limit:
            break
    return snapshot


def _diff_workspace_files(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
    *,
    limit: int = _PYTHON_TOOL_FILE_LIST_LIMIT,
) -> list[dict]:
    changed: list[dict] = []
    for relative_path, state in sorted(after.items()):
        previous_state = before.get(relative_path)
        if previous_state == state:
            continue
        size_bytes, _mtime_ns = state
        changed.append(
            {
                "relative_path": relative_path,
                "size_bytes": size_bytes,
                "change_type": "created" if previous_state is None else "modified",
            }
        )
        if len(changed) >= limit:
            break
    return changed


def _split_configured_paths(value: str) -> list[Path]:
    paths: list[Path] = []
    for chunk in re.split(r"[;\r\n]+", value):
        normalized = chunk.strip().strip('"').strip("'")
        if normalized:
            paths.append(Path(normalized))
    return paths


def _python_tool_runtime_root() -> Path:
    return Path(settings.python_tool_runtime_dir or "C:\\data\\python").resolve()


def _python_tool_venv_dir() -> Path:
    configured = str(settings.python_tool_venv_dir or "").strip()
    return Path(configured).resolve() if configured else (_python_tool_runtime_root() / "aigc-venv").resolve()


def _python_tool_pip_cache_dir() -> Path:
    configured = str(settings.python_tool_pip_cache_dir or "").strip()
    return Path(configured).resolve() if configured else (_python_tool_runtime_root() / "pip-cache").resolve()


def _python_tool_state_path(venv_dir: Path) -> Path:
    return venv_dir / _PYTHON_TOOL_ENV_STATE_FILENAME


def _python_tool_venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _resolve_python_tool_requirements_paths() -> list[Path]:
    configured = str(settings.python_tool_requirements or "").strip()
    if configured:
        return [path.resolve() for path in _split_configured_paths(configured)]

    runtime_requirements = _python_tool_runtime_root() / _PYTHON_TOOL_DEFAULT_REQUIREMENTS_NAME
    if runtime_requirements.exists():
        return [runtime_requirements.resolve()]

    return []


def _hash_python_tool_requirements(requirements_paths: list[Path]) -> str:
    digest = hashlib.sha256()
    digest.update(sys.version.encode("utf-8", errors="replace"))
    digest.update(str(bool(settings.python_tool_system_site_packages)).encode("ascii"))
    for requirements_path in requirements_paths:
        digest.update(str(requirements_path).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(requirements_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _parse_python_requirement_imports(requirements_paths: list[Path]) -> list[str]:
    imports: set[str] = set()
    for requirements_path in requirements_paths:
        try:
            lines = requirements_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.split("#", 1)[0].strip()
            if not line or line.startswith("-") or "://" in line:
                continue
            line = line.split(";", 1)[0].strip()
            match = _PYTHON_REQUIREMENT_NAME_RE.match(line)
            if not match:
                continue
            package_name = match.group(1).lower().replace("_", "-")
            module_name = _PYTHON_REQUIREMENT_IMPORT_NAMES.get(package_name, package_name.replace("-", "_"))
            if module_name:
                imports.add(module_name)
    return sorted(imports)


def _probe_python_tool_imports(
    python_path: Path,
    required_imports: list[str],
    *,
    timeout_seconds: int,
) -> tuple[bool, list[str], str]:
    if not required_imports:
        return True, [], ""

    probe_script = (
        "import importlib, json, sys\n"
        f"mods = {json.dumps(required_imports, ensure_ascii=True)}\n"
        "missing = []\n"
        "for mod in mods:\n"
        "    try:\n"
        "        importlib.import_module(mod)\n"
        "    except Exception as exc:\n"
        "        missing.append({'module': mod, 'error': exc.__class__.__name__ + ': ' + str(exc)[:160]})\n"
        "print(json.dumps({'missing': missing}, ensure_ascii=False))\n"
        "sys.exit(1 if missing else 0)\n"
    )
    try:
        completed = _run_python_runtime_command(
            [str(python_path), "-c", probe_script],
            timeout_seconds=max(15, min(timeout_seconds, 120)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, required_imports, str(exc)

    output = (completed.stdout or completed.stderr or "").strip()
    missing_modules: list[str] = []
    for line in reversed([item.strip() for item in output.splitlines() if item.strip()]):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw_missing = parsed.get("missing") if isinstance(parsed, dict) else None
        if isinstance(raw_missing, list):
            for item in raw_missing:
                if isinstance(item, dict) and str(item.get("module") or "").strip():
                    missing_modules.append(str(item["module"]))
                elif isinstance(item, str) and item.strip():
                    missing_modules.append(item.strip())
            break

    if completed.returncode == 0 and not missing_modules:
        return True, [], output
    if not missing_modules:
        missing_modules = required_imports
    return False, sorted(set(missing_modules)), output


def _read_python_tool_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_python_tool_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(_normalize_tool_payload_value(state), ensure_ascii=False, indent=2), encoding="utf-8")


def _run_python_runtime_command(
    command: list[str],
    *,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        env=env,
    )


def _build_python_tool_runtime_env_patch(runtime: dict) -> dict[str, str]:
    venv_dir = str(runtime.get("venv_dir") or "").strip()
    if not venv_dir:
        return {}

    scripts_dir = Path(venv_dir) / ("Scripts" if os.name == "nt" else "bin")
    path_value = os.environ.get("PATH") or ""
    return {
        "VIRTUAL_ENV": venv_dir,
        "PATH": f"{scripts_dir}{os.pathsep}{path_value}" if path_value else str(scripts_dir),
        "SHOTWRIGHT_PYTHON_RUNTIME_DIR": str(runtime.get("runtime_dir") or ""),
        "SHOTWRIGHT_PYTHON_VENV_DIR": venv_dir,
        "SHOTWRIGHT_PYTHON_REQUIREMENTS": os.pathsep.join(str(path) for path in runtime.get("requirements_paths") or []),
    }


def _sync_python_tool_runtime() -> tuple[Path, dict, dict[str, str], str | None]:
    runtime = {
        "enabled": bool(settings.python_tool_auto_sync_dependencies),
        "runtime_dir": str(_python_tool_runtime_root()),
        "venv_dir": None,
        "pip_cache_dir": None,
        "requirements_paths": [],
        "requirements_fingerprint": None,
        "synced": False,
        "sync_elapsed_ms": 0,
        "using_system_site_packages": bool(settings.python_tool_system_site_packages),
    }

    if not settings.python_tool_auto_sync_dependencies:
        return Path(sys.executable), runtime, {}, None

    requirements_paths = _resolve_python_tool_requirements_paths()
    missing_paths = [str(path) for path in requirements_paths if not path.is_file()]
    if not requirements_paths:
        runtime["enabled"] = False
        return Path(sys.executable), runtime, {}, None
    if missing_paths:
        return Path(sys.executable), runtime, {}, f"Python requirements file not found: {', '.join(missing_paths)}"

    venv_dir = _python_tool_venv_dir()
    venv_python = _python_tool_venv_python(venv_dir)
    pip_cache_dir = _python_tool_pip_cache_dir()
    state_path = _python_tool_state_path(venv_dir)
    fingerprint = _hash_python_tool_requirements(requirements_paths)
    runtime.update(
        {
            "venv_dir": str(venv_dir),
            "pip_cache_dir": str(pip_cache_dir),
            "requirements_paths": [str(path) for path in requirements_paths],
            "requirements_fingerprint": fingerprint,
        }
    )

    started_at = datetime.now(timezone.utc)
    try:
        venv_dir.mkdir(parents=True, exist_ok=True)
        pip_cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Path(sys.executable), runtime, {}, f"Could not prepare Python runtime directories: {exc}"

    sync_timeout_seconds = max(30, int(settings.python_tool_dependency_sync_timeout_seconds or 1800))
    if not venv_python.exists():
        venv_command = [sys.executable, "-m", "venv"]
        if settings.python_tool_system_site_packages:
            venv_command.append("--system-site-packages")
        venv_command.append(str(venv_dir))
        try:
            completed = _run_python_runtime_command(venv_command, timeout_seconds=sync_timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Path(sys.executable), runtime, {}, f"Could not create Python tool venv: {exc}"
        if completed.returncode != 0:
            output = _truncate_text(_redact_sensitive_text((completed.stderr or completed.stdout or "").strip()), 4000)
            return Path(sys.executable), runtime, {}, f"Could not create Python tool venv: {output}"

    state = _read_python_tool_state(state_path)
    required_imports = _parse_python_requirement_imports(requirements_paths)
    runtime["required_imports"] = required_imports
    runtime["import_probe"] = {
        "checked": False,
        "ok": None,
        "missing_imports": [],
    }

    should_install = state.get("requirements_fingerprint") != fingerprint
    if not should_install:
        probe_ok, missing_imports, probe_output = _probe_python_tool_imports(
            venv_python,
            required_imports,
            timeout_seconds=sync_timeout_seconds,
        )
        runtime["import_probe"] = {
            "checked": True,
            "ok": probe_ok,
            "missing_imports": missing_imports,
        }
        if not probe_ok:
            should_install = True
            runtime["repair_reason"] = (
                "missing_imports:" + ",".join(missing_imports)
                if missing_imports
                else _truncate_text(_redact_sensitive_text(probe_output), 1000)
            )

    if should_install:
        pip_env = os.environ.copy()
        pip_env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PIP_CACHE_DIR": str(pip_cache_dir),
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        pip_timeout = str(os.environ.get("PIP_DEFAULT_TIMEOUT") or "120")
        pip_command = [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--progress-bar",
            "off",
            "--retries",
            "10",
            "--timeout",
            pip_timeout,
        ]
        for requirements_path in requirements_paths:
            pip_command.extend(["-r", str(requirements_path)])

        try:
            completed = _run_python_runtime_command(pip_command, timeout_seconds=sync_timeout_seconds, env=pip_env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return venv_python, runtime, _build_python_tool_runtime_env_patch(runtime), f"Could not sync Python packages: {exc}"
        if completed.returncode != 0:
            output = _truncate_text(_redact_sensitive_text((completed.stderr or completed.stdout or "").strip()), 4000)
            return venv_python, runtime, _build_python_tool_runtime_env_patch(runtime), f"Could not sync Python packages: {output}"

        probe_ok, missing_imports, probe_output = _probe_python_tool_imports(
            venv_python,
            required_imports,
            timeout_seconds=sync_timeout_seconds,
        )
        runtime["import_probe"] = {
            "checked": True,
            "ok": probe_ok,
            "missing_imports": missing_imports,
        }
        if not probe_ok:
            output = _truncate_text(_redact_sensitive_text(probe_output), 2000)
            missing = ", ".join(missing_imports) if missing_imports else "unknown modules"
            return (
                venv_python,
                runtime,
                _build_python_tool_runtime_env_patch(runtime),
                f"Python runtime dependencies are still missing after sync: {missing}. {output}",
            )

        runtime["synced"] = True
        _write_python_tool_state(
            state_path,
            {
                "requirements_fingerprint": fingerprint,
                "requirements_paths": [str(path) for path in requirements_paths],
                "required_imports": required_imports,
                "python_executable": str(venv_python),
                "import_probe_ok": True,
                "synced_at": datetime.now(timezone.utc),
            },
        )

    runtime["sync_elapsed_ms"] = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    return venv_python, runtime, _build_python_tool_runtime_env_patch(runtime), None


async def _ensure_python_tool_runtime() -> tuple[Path, dict, dict[str, str], str | None]:
    return await asyncio.to_thread(_ensure_python_tool_runtime_sync)


def _ensure_python_tool_runtime_sync() -> tuple[Path, dict, dict[str, str], str | None]:
    with _PYTHON_TOOL_ENV_LOCK:
        return _sync_python_tool_runtime()


def _sanitize_asset_file_name(value: str | None, fallback_stem: str, suffix: str) -> str:
    raw_name = Path(value).name if value else ""
    raw_stem = Path(raw_name).stem.strip() if raw_name else fallback_stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', raw_stem).strip().strip('.') or fallback_stem

    resolved_suffix = Path(raw_name).suffix.lower() if raw_name else suffix.lower()
    if resolved_suffix and not resolved_suffix.startswith('.'):
        resolved_suffix = f'.{resolved_suffix}'

    return f"{safe_stem}{resolved_suffix or suffix}"


def _jsx_string(value: str) -> str:
    return json.dumps(Path(value).as_posix())


def _should_reuse_generated_project_workspace(project: dict | None) -> bool:
    if not isinstance(project, dict):
        return False

    if str(project.get("origin") or "").strip().lower() != "generated":
        return False

    workspace_dir = str(project.get("workspace_dir") or "").strip()
    entry_aep_file = str(project.get("entry_aep_file") or project.get("filename") or "").strip()
    if not workspace_dir or not entry_aep_file:
        return False

    aep_files = [str(path).strip() for path in (project.get("aep_files") or []) if str(path).strip()]
    return not aep_files and Path(workspace_dir).exists()


def _tts_provider_needs_python_runtime(raw_provider: object) -> bool:
    provider = str(raw_provider or settings.tts_provider or "").strip().lower().replace("-", "_")
    return provider in {"edge", "edge_tts", "microsoft_edge"}


def _build_safe_after_effects_demo_script() -> str:
    return r"""
(function () {
    app.beginUndoGroup("Shotwright safe motion demo");
    var proj = app.project;
    if (!proj) { app.newProject(); proj = app.project; }
    while (proj.items.length > 0) {
        try { proj.items[1].remove(); } catch (clearError) { break; }
    }

    var W = 1920, H = 1080, FPS = 30, DUR = 7;
    var comp = proj.items.addComp("Main", W, H, 1, DUR, FPS);
    comp.bgColor = [0.002, 0.003, 0.012];
    comp.motionBlur = true;
    comp.shutterAngle = 180;

    function p(group, name) { try { return group ? group.property(name) : null; } catch (error) { return null; } }
    function t(layer) { return p(layer, "ADBE Transform Group"); }
    function setv(prop, value) { try { if (prop) { prop.setValue(value); } } catch (error) {} }
    function key(prop, time, value) { try { if (prop) { prop.setValueAtTime(time, value); } } catch (error) {} }
    function set2d(layer, x, y) { setv(p(t(layer), "ADBE Position"), [x, y]); }
    function set3d(layer, x, y, z) { layer.threeDLayer = true; setv(p(t(layer), "ADBE Position"), [x, y, z]); }
    function root(layer) { return p(layer, "ADBE Root Vectors Group"); }
    function glow(layer, radius, intensity) {
        try {
            var fx = p(layer, "ADBE Effect Parade").addProperty("ADBE Glow");
            setv(p(fx, "ADBE Glow-0002"), radius);
            setv(p(fx, "ADBE Glow-0003"), intensity);
        } catch (error) {}
    }
    function addPath(layer, name, points, color, width, opacity) {
        try {
            var group = root(layer).addProperty("ADBE Vector Group");
            group.name = name;
            var contents = p(group, "ADBE Vectors Group");
            var shapeGroup = contents.addProperty("ADBE Vector Shape - Group");
            var shape = new Shape();
            shape.vertices = points;
            shape.inTangents = [];
            shape.outTangents = [];
            for (var i = 0; i < points.length; i += 1) { shape.inTangents.push([0, 0]); shape.outTangents.push([0, 0]); }
            shape.closed = false;
            p(shapeGroup, "ADBE Vector Shape").setValue(shape);
            var stroke = contents.addProperty("ADBE Vector Graphic - Stroke");
            setv(p(stroke, "ADBE Vector Stroke Color"), color);
            setv(p(stroke, "ADBE Vector Stroke Width"), width);
            setv(p(stroke, "ADBE Vector Stroke Opacity"), opacity);
            return contents;
        } catch (error) { return null; }
    }
    function addEllipse(layer, name, position, size, color, opacity, strokeWidth) {
        try {
            var group = root(layer).addProperty("ADBE Vector Group");
            group.name = name;
            var contents = p(group, "ADBE Vectors Group");
            var ellipse = contents.addProperty("ADBE Vector Shape - Ellipse");
            setv(p(ellipse, "ADBE Vector Ellipse Position"), position);
            setv(p(ellipse, "ADBE Vector Ellipse Size"), size);
            if (strokeWidth && strokeWidth > 0) {
                var stroke = contents.addProperty("ADBE Vector Graphic - Stroke");
                setv(p(stroke, "ADBE Vector Stroke Color"), color);
                setv(p(stroke, "ADBE Vector Stroke Width"), strokeWidth);
                setv(p(stroke, "ADBE Vector Stroke Opacity"), opacity);
            } else {
                var fill = contents.addProperty("ADBE Vector Graphic - Fill");
                setv(p(fill, "ADBE Vector Fill Color"), color);
                setv(p(fill, "ADBE Vector Fill Opacity"), opacity);
            }
            return contents;
        } catch (error) { return null; }
    }

    var bg = comp.layers.addSolid([0.002, 0.003, 0.012], "Deep navy stage base", W, H, 1, DUR);
    bg.moveToEnd();

    function starLayer(name, count, z, seed, color, size, drift) {
        var layer = comp.layers.addShape();
        layer.name = name;
        set3d(layer, 0, 0, z);
        for (var i = 0; i < count; i += 1) {
            var x = Math.sin((i + 1) * (12.9898 + seed)) * 43758.5453;
            var y = Math.sin((i + 1) * (78.233 + seed)) * 24634.6345;
            x = (x - Math.floor(x)) * W;
            y = (y - Math.floor(y)) * H;
            var s = size * (0.45 + ((i * 37) % 100) / 100);
            addEllipse(layer, "star " + i, [x, y], [s, s], color, 35 + ((i * 11) % 55), 0);
        }
        p(t(layer), "ADBE Position").expression = "value + [Math.sin(time*0.21+" + seed + ")*" + drift + ", Math.cos(time*0.13+" + seed + ")*" + (drift * 0.5) + ", 0];";
        glow(layer, 24, 0.8);
    }
    starLayer("Far cyan stardust parallax", 72, 900, 1.3, [0.25, 0.8, 1], 4, 18);
    starLayer("Mid magenta stardust parallax", 58, 430, 3.7, [1, 0.25, 0.9], 5, 32);
    starLayer("Near white spark dust", 38, -60, 5.2, [0.85, 1, 0.95], 6, 52);

    var grid = comp.layers.addShape();
    grid.name = "Neon perspective floor grid";
    set3d(grid, 0, 0, 260);
    for (var gy = 1; gy <= 12; gy += 1) {
        var f = gy / 12;
        var y = 540 + Math.pow(f, 1.7) * 620;
        var half = 90 + f * 1240;
        addPath(grid, "horizon row " + gy, [[960 - half, y], [960 + half, y]], [0, 0.8, 1], 2.2, 58);
    }
    for (var gx = -520; gx <= 2440; gx += 160) {
        addPath(grid, "vanish ray " + gx, [[960, 430], [gx, 1240]], [0.7, 0.1, 1], 1.6, 46);
    }
    p(t(grid), "ADBE Position").expression = "value + [0, (time*24)%55, 0];";
    glow(grid, 36, 1.1);

    function orbit(name, size, color, z, rx, ry, rz, speed) {
        var layer = comp.layers.addShape();
        layer.name = name;
        set3d(layer, W / 2, H / 2, z);
        var contents = addEllipse(layer, "orbit stroke", [0, 0], size, color, 88, 4);
        try {
            var trim = contents.addProperty("ADBE Vector Filter - Trim");
            p(trim, "ADBE Vector Trim End").setValue(72);
            p(trim, "ADBE Vector Trim Offset").expression = "time*" + speed;
        } catch (trimError) {}
        setv(p(t(layer), "ADBE Rotate X"), rx);
        setv(p(t(layer), "ADBE Rotate Y"), ry);
        setv(p(t(layer), "ADBE Rotate Z"), rz);
        p(t(layer), "ADBE Rotate Z").expression = "value + time*" + (speed * 0.12);
        layer.motionBlur = true;
        glow(layer, 48, 1.4);
    }
    orbit("Cyan orbit ring A", [650, 176], [0, 0.95, 1], -160, 68, 0, 0, 92);
    orbit("Magenta orbit ring B", [520, 315], [1, 0.15, 0.88], -120, 54, 24, 16, -116);
    orbit("Lime orbit ring C", [780, 120], [0.4, 1, 0.55], -210, 76, -20, -12, 156);

    var hud = comp.layers.addShape();
    hud.name = "HUD targeting frame";
    set3d(hud, W / 2, H / 2, -130);
    addPath(hud, "corner tl a", [[-380, -165], [-298, -165]], [0, 0.9, 1], 3, 78);
    addPath(hud, "corner tl b", [[-380, -165], [-380, -83]], [0, 0.9, 1], 3, 78);
    addPath(hud, "corner br a", [[380, 165], [298, 165]], [1, 0.15, 0.9], 3, 78);
    addPath(hud, "corner br b", [[380, 165], [380, 83]], [1, 0.15, 0.9], 3, 78);
    addPath(hud, "cross x", [[-78, 0], [78, 0]], [0.65, 1, 0.86], 1.5, 65);
    addPath(hud, "cross y", [[0, -78], [0, 78]], [0.65, 1, 0.86], 1.5, 65);
    addEllipse(hud, "inner reticle", [0, 0], [124, 124], [0.45, 1, 0.9], 62, 2);
    p(t(hud), "ADBE Rotate Z").expression = "Math.sin(time*1.3)*2.5";
    glow(hud, 38, 1.2);

    function textDoc(layer, size, color, tracking) {
        var docProp = p(p(layer, "ADBE Text Properties"), "ADBE Text Document");
        var doc = docProp.value;
        doc.fontSize = size;
        doc.fillColor = color;
        doc.justification = ParagraphJustification.CENTER_JUSTIFY;
        try { doc.tracking = tracking; } catch (trackingError) {}
        docProp.setValue(doc);
        return docProp;
    }
    function revealText(label, start, y, size, color, tracking) {
        var layer = comp.layers.addText(label);
        layer.name = "Type reveal " + label;
        set3d(layer, W / 2, y, -120);
        var docProp = textDoc(layer, size, color, tracking);
        docProp.expression = "var full='" + label + "'; var c=Math.floor(linear(time," + start + "," + (start + 0.82) + ",0,full.length)); c=Math.max(0,Math.min(full.length,c)); full.substr(0,c);";
        key(p(t(layer), "ADBE Opacity"), start - 0.05, 0);
        key(p(t(layer), "ADBE Opacity"), start + 0.16, 100);
        p(t(layer), "ADBE Position").expression = "value + [Math.sin(time*17+index)*5, Math.sin(time*23+index)*2, 0];";
        layer.motionBlur = true;
        glow(layer, 58, 1.5);
    }
    revealText("SHOTWRIGHT", 0.35, 240, 118, [0.55, 0.95, 1], 150);
    revealText("CODEX", 1.12, 350, 104, [1, 0.28, 0.95], 205);
    revealText("AE SKILL", 1.82, 456, 76, [0.42, 1, 0.72], 130);

    function countText(label, t0, t1, size) {
        var layer = comp.layers.addText(label);
        layer.name = "Countdown " + label;
        set3d(layer, W / 2, 620, -145);
        textDoc(layer, size, label === "LAUNCH" ? [0.95, 1, 0.72] : [0.72, 0.98, 1], label === "LAUNCH" ? 70 : 20);
        key(p(t(layer), "ADBE Opacity"), t0 - 0.04, 0);
        key(p(t(layer), "ADBE Opacity"), t0 + 0.08, 100);
        key(p(t(layer), "ADBE Opacity"), t1 - 0.12, 100);
        key(p(t(layer), "ADBE Opacity"), t1, 0);
        p(t(layer), "ADBE Position").expression = "value + [Math.sin(time*19+index)*4, Math.cos(time*29+index)*2, 0];";
        glow(layer, 64, 1.7);
    }
    countText("03", 0.52, 2.0, 218);
    countText("02", 2.0, 3.5, 218);
    countText("01", 3.5, 5.0, 218);
    countText("LAUNCH", 5.0, 7.0, 132);

    function burst(time, index, color) {
        var wave = comp.layers.addShape();
        wave.name = "Radial shockwave " + index;
        set3d(wave, W / 2, H / 2, -105);
        addEllipse(wave, "impact wave", [0, 0], [420, 420], color, 95, 4);
        key(p(t(wave), "ADBE Scale"), time, [18, 18, 18]);
        key(p(t(wave), "ADBE Scale"), time + 0.5, [190, 190, 190]);
        key(p(t(wave), "ADBE Opacity"), time, 92);
        key(p(t(wave), "ADBE Opacity"), time + 0.5, 0);
        glow(wave, 70, 1.6);
        var scan = comp.layers.addShape();
        scan.name = "Scanline burst " + index;
        for (var sy = 0; sy < H; sy += 28) {
            addPath(scan, "scan " + sy, [[0, sy], [W, sy]], color, 1.2, 32);
        }
        key(p(t(scan), "ADBE Opacity"), time - 0.03, 0);
        key(p(t(scan), "ADBE Opacity"), time + 0.03, 70);
        key(p(t(scan), "ADBE Opacity"), time + 0.34, 0);
        glow(scan, 32, 1.1);
    }
    burst(0.52, 1, [0, 0.9, 1]);
    burst(2.0, 2, [1, 0.12, 0.9]);
    burst(3.5, 3, [0.45, 1, 0.55]);
    burst(5.0, 4, [1, 0.9, 0.25]);

    var cam = comp.layers.addCamera("Drift push camera", [W / 2, H / 2]);
    key(p(t(cam), "ADBE Position"), 0, [940, 548, -1740]);
    key(p(t(cam), "ADBE Position"), DUR, [1040, 526, -1240]);
    key(p(t(cam), "ADBE Point of Interest"), 0, [960, 545, 0]);
    key(p(t(cam), "ADBE Point of Interest"), DUR, [960, 560, 80]);
    p(t(cam), "ADBE Position").expression = "value + [Math.sin(time*0.73)*18, Math.sin(time*0.41)*7, 0];";
    try { p(p(cam, "ADBE Camera Options Group"), "ADBE Camera Zoom").setValue(1450); } catch (zoomError) {}

    comp.openInViewer();
    var savePath = $.getenv("SHOTWRIGHT_PROJECT_FILE");
    if (savePath) { proj.save(new File(savePath)); }
    app.endUndoGroup();
}());
""".strip()


async def _list_session_image_attachments(session_id: str, *, limit: int = 8) -> list[dict]:
    attachments: list[dict] = []
    seen_paths: set[str] = set()

    cursor = get_message_collection().find(
        {"session_id": session_id},
        {"metadata.attachments": 1, "created_at": 1},
    ).sort("created_at", -1)

    async for message_doc in cursor:
        metadata = message_doc.get("metadata") or {}
        for attachment in metadata.get("attachments") or []:
            if not isinstance(attachment, dict) or attachment.get("type") != "image":
                continue

            file_path = str(attachment.get("file_path") or "").strip()
            if not file_path:
                continue

            resolved_path = Path(file_path)
            if not resolved_path.exists():
                continue

            dedupe_key = str(resolved_path).lower()
            if dedupe_key in seen_paths:
                continue
            seen_paths.add(dedupe_key)

            attachments.append(
                {
                    "file_path": str(resolved_path),
                    "display_name": attachment.get("display_name") or resolved_path.name,
                    "mime_type": attachment.get("mime_type"),
                    "shared_relative_path": attachment.get("shared_relative_path"),
                    "workspace_relative_path": attachment.get("workspace_relative_path"),
                    "width": attachment.get("width"),
                    "height": attachment.get("height"),
                    "size_bytes": attachment.get("size_bytes"),
                    "created_at": message_doc.get("created_at"),
                }
            )
            if len(attachments) >= limit:
                return attachments

    return attachments


async def list_session_image_attachments(session_id: str, *, limit: int = 8) -> list[dict]:
    return await _list_session_image_attachments(session_id, limit=limit)


def _copy_asset_into_project(
    project: dict,
    source_path: Path,
    *,
    display_name: str | None = None,
    asset_name: str | None = None,
    target_directory: Path = _REFERENCE_ASSET_DIRECTORY,
) -> dict:
    if not source_path.exists():
        raise FileNotFoundError(f"Reference asset not found at {source_path}")

    project_root = Path(project["workspace_dir"])
    destination_dir = project_root / target_directory
    destination_dir.mkdir(parents=True, exist_ok=True)

    suffix = source_path.suffix.lower() or ".bin"
    destination_name = _sanitize_asset_file_name(asset_name or display_name, "reference-image", suffix)
    destination_path = destination_dir / destination_name

    if str(source_path.resolve()).lower() != str(destination_path.resolve()).lower():
        shutil.copy2(source_path, destination_path)

    return {
        "source_path": str(source_path),
        "project_asset_path": str(destination_path),
        "project_relative_path": destination_path.relative_to(project_root).as_posix(),
        "display_name": display_name or source_path.name,
    }


async def _stage_session_image_attachments(
    session_id: str,
    project: dict,
    *,
    latest_only: bool = True,
    asset_name: str | None = None,
) -> list[dict]:
    image_attachments = await _list_session_image_attachments(session_id, limit=1 if latest_only else 8)
    if not image_attachments:
        return []

    staged_assets: list[dict] = []
    total = len(image_attachments)
    for index, attachment in enumerate(image_attachments, start=1):
        source_path = Path(str(attachment["file_path"]))
        desired_name = asset_name
        if not latest_only and total > 1 and desired_name:
            suffix = source_path.suffix.lower() or ".bin"
            desired_name = f"{Path(desired_name).stem}-{index:02d}{suffix}"

        staged_assets.append(
            _copy_asset_into_project(
                project,
                source_path,
                display_name=str(attachment.get("display_name") or source_path.name),
                asset_name=desired_name,
            )
        )

    return staged_assets


def _build_empty_project_jsx() -> str:
    return "\n".join(
        [
            "app.beginSuppressDialogs();",
            "if (typeof CloseOptions !== \"undefined\" && app.project && typeof app.project.close === \"function\") {",
            "    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);",
            "}",
        ]
    )


def _build_reference_composition_jsx(
    *,
    reference_asset_path: str,
    composition_name: str,
    width: int,
    height: int,
    duration_seconds: float,
    frame_rate: float,
    fit_mode: str,
    reset_existing: bool,
) -> str:
    normalized_fit_mode = "contain" if fit_mode == "contain" else "cover"

    return "\n".join(
        [
            "app.beginSuppressDialogs();",
            "function normalizePath(value) {",
            "    if (!value) { return \"\"; }",
            "    return value.toString().replace(/\\\\/g, \"/\").toLowerCase();",
            "}",
            "function findCompByName(name) {",
            "    if (!app.project) { return null; }",
            "    for (var itemIndex = 1; itemIndex <= app.project.items.length; itemIndex += 1) {",
            "        var item = app.project.items[itemIndex];",
            "        if (item instanceof CompItem && item.name === name) {",
            "            return item;",
            "        }",
            "    }",
            "    return null;",
            "}",
            "function findFootageByPath(targetPath) {",
            "    var normalizedTargetPath = normalizePath(targetPath);",
            "    if (!normalizedTargetPath || !app.project) { return null; }",
            "    for (var itemIndex = 1; itemIndex <= app.project.items.length; itemIndex += 1) {",
            "        var item = app.project.items[itemIndex];",
            "        if (!(item instanceof FootageItem) || !item.file) { continue; }",
            "        if (normalizePath(item.file.fsName) === normalizedTargetPath) {",
            "            return item;",
            "        }",
            "    }",
            "    return null;",
            "}",
            "function removeLayerByName(comp, name) {",
            "    if (!comp) { return; }",
            "    for (var layerIndex = comp.numLayers; layerIndex >= 1; layerIndex -= 1) {",
            "        var layer = comp.layer(layerIndex);",
            "        if (layer && layer.name === name) {",
            "            layer.remove();",
            "        }",
            "    }",
            "}",
            "function fitLayerToComp(layer, comp, mode) {",
            "    if (!layer || !layer.source || !comp) { return; }",
            "    var sourceWidth = layer.source.width || comp.width;",
            "    var sourceHeight = layer.source.height || comp.height;",
            "    if (!sourceWidth || !sourceHeight) { return; }",
            "    var scaleX = (comp.width / sourceWidth) * 100;",
            "    var scaleY = (comp.height / sourceHeight) * 100;",
            "    var uniformScale = mode === \"contain\" ? Math.min(scaleX, scaleY) : Math.max(scaleX, scaleY);",
            "    try { layer.property(\"Anchor Point\").setValue([sourceWidth / 2, sourceHeight / 2]); } catch (anchorError) {}",
            "    layer.property(\"Scale\").setValue([uniformScale, uniformScale]);",
            "    layer.property(\"Position\").setValue([comp.width / 2, comp.height / 2]);",
            "}",
            f"var referenceFile = new File({_jsx_string(reference_asset_path)});",
            "if (!referenceFile.exists) { throw new Error(\"Reference image not found: \" + referenceFile.fsName); }",
            f"var compositionName = {json.dumps(composition_name)};",
            f"var fitMode = {json.dumps(normalized_fit_mode)};",
            f"var resetExisting = {'true' if reset_existing else 'false'};",
            "var footage = findFootageByPath(referenceFile.fsName);",
            "if (!footage) {",
            "    footage = app.project.importFile(new ImportOptions(referenceFile));",
            "}",
            "var comp = findCompByName(compositionName);",
            "if (!comp) {",
            f"    comp = app.project.items.addComp(compositionName, {max(16, int(width))}, {max(16, int(height))}, 1, {max(1.0, float(duration_seconds))}, {max(1.0, float(frame_rate))});",
            "} else {",
            f"    comp.width = {max(16, int(width))};",
            f"    comp.height = {max(16, int(height))};",
            f"    comp.duration = {max(1.0, float(duration_seconds))};",
            f"    comp.frameRate = {max(1.0, float(frame_rate))};",
            "}",
            "if (resetExisting) {",
            "    for (var layerIndex = comp.numLayers; layerIndex >= 1; layerIndex -= 1) {",
            "        comp.layer(layerIndex).remove();",
            "    }",
            "} else {",
            "    removeLayerByName(comp, \"shotwright_reference_image\");",
            "}",
            "var imageLayer = comp.layers.add(footage);",
            "imageLayer.name = \"shotwright_reference_image\";",
            "fitLayerToComp(imageLayer, comp, fitMode);",
            "comp.openInViewer();",
        ]
    )


def _coerce_timecode_seconds(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            return 0.0
    parts = text.split(":")
    try:
        seconds = float(parts[-1])
        minutes = int(parts[-2]) if len(parts) >= 2 else 0
        hours = int(parts[-3]) if len(parts) >= 3 else 0
    except ValueError:
        return 0.0
    return hours * 3600 + minutes * 60 + seconds


_LRC_LINE_RE = re.compile(r"^\s*\[(?P<time>\d{1,2}:\d{2}(?:\.\d+)?)\]\s*(?P<text>.*?)\s*$")


def _segment_lyric_line(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return []
    spaced_chunks = [chunk.strip() for chunk in cleaned.split(" ") if chunk.strip()]
    if len(spaced_chunks) > 1:
        return spaced_chunks

    punctuation_chunks = [
        chunk.strip()
        for chunk in re.split(r"[，,。.!！?？、;；:：]+", cleaned)
        if chunk.strip()
    ]
    if len(punctuation_chunks) > 1:
        return punctuation_chunks
    if len(cleaned) <= 6:
        return [cleaned]

    midpoint = max(2, min(len(cleaned) - 2, len(cleaned) // 2))
    return [cleaned[:midpoint], cleaned[midpoint:]]


def _build_lyrics_mapping_from_lrc(
    lyrics_lrc: str,
    *,
    source_start_timecode: str,
    duration_seconds: float,
) -> dict:
    base_seconds = _coerce_timecode_seconds(source_start_timecode)
    lines: list[tuple[str, float, str]] = []
    for raw_line in str(lyrics_lrc or "").splitlines():
        match = _LRC_LINE_RE.match(raw_line)
        if not match:
            continue
        text = match.group("text").strip()
        if not text:
            continue
        source_timecode = match.group("time")
        lines.append((source_timecode, _coerce_timecode_seconds(source_timecode), text))

    if not lines:
        return {
            "base_source_timecode": source_start_timecode,
            "duration_seconds": duration_seconds,
            "entries": [],
        }

    if base_seconds <= 0:
        base_seconds = lines[0][1]
        source_start_timecode = lines[0][0]

    entries: list[dict] = []
    for line_index, (source_timecode, source_seconds, text) in enumerate(lines, start=1):
        chunks = _segment_lyric_line(text)
        for chunk_index, chunk in enumerate(chunks, start=1):
            entries.append(
                {
                    "line_index": line_index,
                    "chunk_index": chunk_index,
                    "text": chunk,
                    "summary": chunk[:8],
                    "source_timecode": source_timecode,
                    "source_seconds": round(source_seconds, 2),
                    "comp_time_seconds": round(source_seconds - base_seconds + (chunk_index - 1) * 0.42, 2),
                }
            )

    return {
        "base_source_timecode": source_start_timecode,
        "duration_seconds": duration_seconds,
        "entries": entries,
    }


async def _find_recent_lrc_text(session_id: str, *, min_lines: int = 2) -> str:
    try:
        cursor = (
            get_message_collection()
            .find({"session_id": session_id}, {"content": 1, "created_at": 1})
            .sort("created_at", -1)
        )
        scanned = 0
        async for message_doc in cursor:
            scanned += 1
            text = str(message_doc.get("content") or "")
            if len(_LRC_LINE_RE.findall(text)) >= min_lines:
                return text
            if scanned >= 24:
                break
    except Exception:
        return ""
    return ""


def _write_lyrics_mv_placeholder_audio(audio_path: Path, *, duration_seconds: float, sample_rate: int = 44100) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = max(1, int(float(duration_seconds) * sample_rate))
    frames = bytearray()
    for index in range(total_frames):
        t = index / sample_rate
        pad = (
            0.08 * math.sin(2 * math.pi * 110.0 * t)
            + 0.045 * math.sin(2 * math.pi * 146.83 * t + 0.35)
            + 0.028 * math.sin(2 * math.pi * 196.0 * t + 0.9)
        )
        shimmer = 0.018 * math.sin(2 * math.pi * 523.25 * t) * math.exp(-((t % 3.2) * 2.2))
        pulse = 0.045 * math.sin(2 * math.pi * 68.0 * t) * math.exp(-((t % 1.6) * 5.6))
        value = max(-0.92, min(0.92, pad + shimmer + pulse))
        frames.extend(int(value * 32767).to_bytes(2, byteorder="little", signed=True))

    with wave.open(str(audio_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


def _load_lyrics_mapping_from_payload(payload: object, *, source_start_timecode: str) -> list[dict]:
    raw_entries = payload.get("entries") if isinstance(payload, dict) else []
    if not isinstance(raw_entries, list):
        return []

    base_seconds = _coerce_timecode_seconds(
        payload.get("base_source_timecode") if isinstance(payload, dict) else source_start_timecode
    ) or _coerce_timecode_seconds(source_start_timecode)
    entries: list[dict] = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("summary") or "").strip()
        if not text:
            continue
        source_timecode = str(item.get("source_timecode") or "").strip()
        source_seconds = item.get("source_seconds")
        if not isinstance(source_seconds, (int, float)):
            source_seconds = _coerce_timecode_seconds(source_timecode)
        comp_time = item.get("comp_time_seconds")
        if not isinstance(comp_time, (int, float)):
            comp_time = float(source_seconds) - base_seconds
        entries.append(
            {
                "index": index,
                "line_index": item.get("line_index"),
                "chunk_index": item.get("chunk_index"),
                "text": text,
                "summary": str(item.get("summary") or text[:8]),
                "source_timecode": source_timecode,
                "source_seconds": round(float(source_seconds), 2),
                "comp_time_seconds": round(float(comp_time), 2),
            }
        )
    return entries


def _load_lyrics_mapping(mapping_path: Path, *, source_start_timecode: str) -> list[dict]:
    payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    return _load_lyrics_mapping_from_payload(payload, source_start_timecode=source_start_timecode)


def _select_lyrics_mv_preview_entries(entries: list[dict], duration_seconds: float) -> list[dict]:
    if not entries:
        return []
    if len(entries) <= 30:
        selected = list(entries)
    else:
        middle_start = max(12, (len(entries) // 2) - 6)
        selected = []
        seen: set[int] = set()
        for item in [*entries[:12], *entries[middle_start : middle_start + 10], *entries[-12:]]:
            index = int(item.get("index") or 0)
            if index in seen:
                continue
            seen.add(index)
            selected.append(item)

    usable_duration = max(12.0, float(duration_seconds) - 4.0)
    spacing = usable_duration / max(1, len(selected))
    normalized: list[dict] = []
    for index, item in enumerate(selected):
        normalized_item = dict(item)
        normalized_item["preview_time_seconds"] = round(1.2 + index * spacing + (index % 2) * 0.18, 2)
        normalized_item["preview_duration_seconds"] = 2.65 if index % 3 else 3.1
        normalized.append(normalized_item)
    return normalized


def _build_lyrics_mv_project_jsx(
    *,
    project_root: Path,
    aep_path: Path,
    mapping_entries: list[dict],
    audio_path: Path | None,
    image_paths: dict[str, Path],
    composition_name: str,
    width: int,
    height: int,
    duration_seconds: float,
    frame_rate: float,
    measurement_report_path: Path,
) -> str:
    entries_json = json.dumps(mapping_entries, ensure_ascii=False)
    images_json = json.dumps({key: path.as_posix() for key, path in image_paths.items() if path.is_file()})
    audio_json = json.dumps(audio_path.as_posix() if audio_path and audio_path.is_file() else "")
    positions_json = json.dumps(
        [
            {"x": int(width * 0.50), "y": int(height * 0.36), "boxW": int(width * 0.78), "boxH": 150, "size": 54, "tone": "blue"},
            {"x": int(width * 0.34), "y": int(height * 0.50), "boxW": int(width * 0.70), "boxH": 150, "size": 56, "tone": "ink"},
            {"x": int(width * 0.64), "y": int(height * 0.61), "boxW": int(width * 0.70), "boxH": 150, "size": 58, "tone": "blue"},
            {"x": int(width * 0.50), "y": int(height * 0.72), "boxW": int(width * 0.82), "boxH": 160, "size": 60, "tone": "jade"},
            {"x": int(width * 0.36), "y": int(height * 0.81), "boxW": int(width * 0.68), "boxH": 150, "size": 52, "tone": "ink"},
            {"x": int(width * 0.64), "y": int(height * 0.43), "boxW": int(width * 0.64), "boxH": 140, "size": 50, "tone": "blue"},
        ]
    )
    return "\n".join(
        [
            "app.beginSuppressDialogs();",
            "app.beginUndoGroup('Shotwright lyric motion MV');",
            f"var W = {int(width)};",
            f"var H = {int(height)};",
            f"var DUR = {float(duration_seconds):.3f};",
            f"var FPS = {float(frame_rate):.3f};",
            f"var compName = {json.dumps(composition_name)};",
            f"var saveFile = new File({_jsx_string(str(aep_path))});",
            f"var measurementReportFile = new File({_jsx_string(str(measurement_report_path))});",
            f"var entries = {entries_json};",
            f"var positions = {positions_json};",
            f"var imagePaths = {images_json};",
            f"var audioPath = {audio_json};",
            "function normalizePath(value) { return value ? value.toString().replace(/\\\\/g, '/').toLowerCase() : ''; }",
            "function findCompByName(name) { for (var i = 1; i <= app.project.items.length; i += 1) { var item = app.project.items[i]; if (item instanceof CompItem && item.name === name) { return item; } } return null; }",
            "function findFootageByPath(path) { var target = normalizePath(path); for (var i = 1; i <= app.project.items.length; i += 1) { var item = app.project.items[i]; if (item instanceof FootageItem && item.file && normalizePath(item.file.fsName) === target) { return item; } } return null; }",
            "function importFootage(path) { if (!path) { return null; } var file = new File(path); if (!file.exists) { return null; } var existing = findFootageByPath(file.fsName); return existing || app.project.importFile(new ImportOptions(file)); }",
            "function fitLayer(layer, mode) { if (!layer || !layer.source) { return; } var sx = W / layer.source.width * 100; var sy = H / layer.source.height * 100; var s = mode === 'contain' ? Math.min(sx, sy) : Math.max(sx, sy); layer.property('Anchor Point').setValue([layer.source.width / 2, layer.source.height / 2]); layer.property('Scale').setValue([s, s]); layer.property('Position').setValue([W / 2, H / 2]); }",
            "function jsonEscape(value) { var text = value === null || typeof value === 'undefined' ? '' : value.toString(); return text.replace(/\\\\/g, '\\\\\\\\').replace(/\"/g, '\\\\\"').replace(/\\r/g, '\\\\r').replace(/\\n/g, '\\\\n').replace(/\\t/g, '\\\\t'); }",
            "function isArray(value) { return Object.prototype.toString.call(value) === '[object Array]'; }",
            "function jsonStringify(value) { var valueType = typeof value; if (value === null || valueType === 'undefined') { return 'null'; } if (valueType === 'string') { return '\"' + jsonEscape(value) + '\"'; } if (valueType === 'number') { return isFinite(value) ? String(value) : 'null'; } if (valueType === 'boolean') { return value ? 'true' : 'false'; } if (isArray(value)) { var arrayParts = []; for (var ai = 0; ai < value.length; ai += 1) { arrayParts.push(jsonStringify(value[ai])); } return '[' + arrayParts.join(',') + ']'; } if (valueType === 'object') { var objectParts = []; for (var key in value) { if (!value.hasOwnProperty || value.hasOwnProperty(key)) { objectParts.push('\"' + jsonEscape(key) + '\":' + jsonStringify(value[key])); } } return '{' + objectParts.join(',') + '}'; } return 'null'; }",
            "function chooseFont(preferred) { var fonts = app.fonts && app.fonts.allFonts ? app.fonts.allFonts : []; for (var p = 0; p < preferred.length; p += 1) { for (var i = 0; i < fonts.length; i += 1) { var ps = fonts[i].postScriptName || fonts[i].name || ''; if (ps === preferred[p]) { return preferred[p]; } } } return preferred[preferred.length - 1]; }",
            "function setEase(prop) { try { var easeIn = new KeyframeEase(0, 60); var easeOut = new KeyframeEase(0, 70); for (var k = 1; k <= prop.numKeys; k += 1) { prop.setTemporalEaseAtKey(k, [easeIn], [easeOut]); } } catch (err) {} }",
            "function applyTextStyle(layer, text, slot, index) { var textProp = layer.property('Source Text'); var doc = textProp.value; doc.text = text; try { doc.font = chooseFont(index % 4 === 0 ? ['NotoSerifSC-Bold','NotoSerifSC-Medium','NotoSansSC-Bold','SimSun'] : ['NotoSansSC-Bold','NotoSansSC-Medium','MicrosoftYaHei-Bold','SimHei']); } catch (fontErr) {} doc.fontSize = slot.size; doc.leading = slot.size * 1.12; doc.applyFill = true; doc.applyStroke = true; doc.strokeWidth = 1.6; if (slot.tone === 'jade') { doc.fillColor = [0.00, 0.35, 0.34]; } else if (slot.tone === 'blue') { doc.fillColor = [0.02, 0.22, 0.43]; } else { doc.fillColor = [0.07, 0.11, 0.16]; } doc.strokeColor = [0.98, 0.99, 0.96]; doc.justification = ParagraphJustification.CENTER_JUSTIFY; textProp.setValue(doc); }",
            "var oldComp = findCompByName(compName); if (oldComp) { oldComp.remove(); }",
            "var comp = app.project.items.addComp(compName, W, H, 1, DUR, FPS); comp.bgColor = [0.95, 0.97, 0.95]; comp.motionBlur = true; comp.shutterAngle = 180;",
            "var bg = comp.layers.addSolid([0.95, 0.97, 0.95], 'bright_porcelain_base', W, H, 1, DUR); try { var bgNoise = bg.property('Effects').addProperty('ADBE Fractal Noise'); bgNoise.property('Contrast').setValue(30); bgNoise.property('Brightness').setValue(8); bgNoise.property('Transform').property('Scale').setValue(210); } catch (noiseErr) {}",
            "var glowA = comp.layers.addSolid([0.78, 0.91, 0.92], 'cyan_porcelain_light_sweep', W, H, 1, DUR); glowA.property('Opacity').setValueAtTime(0, 34); glowA.property('Opacity').setValueAtTime(DUR * 0.45, 58); glowA.property('Opacity').setValueAtTime(DUR, 32); try { glowA.blendingMode = BlendingMode.SOFT_LIGHT; var rampA = glowA.property('Effects').addProperty('ADBE Ramp'); rampA.property('Start of Ramp').setValue([W*0.2, H*0.1]); rampA.property('End of Ramp').setValue([W*0.9, H*0.9]); rampA.property('Start Color').setValue([0.96, 1.0, 0.98]); rampA.property('End Color').setValue([0.55, 0.78, 0.86]); } catch (glowErr) {}",
            "var washItem = importFootage(imagePaths.porcelain || ''); if (washItem) { var wash = comp.layers.add(washItem); wash.name = 'porcelain_rain_wash_light'; fitLayer(wash, 'cover'); wash.property('Opacity').setValue(34); try { wash.blendingMode = BlendingMode.SOFT_LIGHT; } catch (blendErr) {} }",
            "var inkItem = importFootage(imagePaths.ink || ''); if (inkItem) { var ink = comp.layers.add(inkItem); ink.name = 'pale_ink_diffusion_depth'; fitLayer(ink, 'cover'); ink.property('Opacity').setValueAtTime(0, 10); ink.property('Opacity').setValueAtTime(DUR * 0.55, 22); ink.property('Opacity').setValueAtTime(DUR, 16); try { ink.blendingMode = BlendingMode.MULTIPLY; } catch (blendErr2) {} }",
            "var crackItem = importFootage(imagePaths.crack || ''); if (crackItem) { var crack = comp.layers.add(crackItem); crack.name = 'subtle_ceramic_memory_overlay'; fitLayer(crack, 'cover'); crack.property('Opacity').setValueAtTime(0, 0); crack.property('Opacity').setValueAtTime(DUR * 0.78, 18); crack.property('Opacity').setValueAtTime(DUR, 8); try { crack.blendingMode = BlendingMode.SOFT_LIGHT; } catch (crackBlendErr) {} }",
            "for (var r = 0; r < 11; r += 1) { var ring = comp.layers.addShape(); ring.name = 'porcelain_water_ring_' + (r + 1); var root = ring.property('Contents'); var ellipse = root.addProperty('ADBE Vector Shape - Ellipse'); ellipse.property('Size').setValue([130 + r * 62, 46 + r * 24]); var stroke = root.addProperty('ADBE Vector Graphic - Stroke'); stroke.property('Color').setValue([0.03, 0.32, 0.48]); stroke.property('Opacity').setValue(32); stroke.property('Stroke Width').setValue(2.5); ring.property('Position').setValue([W * (0.16 + (r % 3) * 0.32), H * (0.20 + (r % 4) * 0.18)]); ring.property('Opacity').setValueAtTime(Math.max(0, r * 3.6 - 1), 0); ring.property('Opacity').setValueAtTime(r * 3.6, 38); ring.property('Opacity').setValueAtTime(Math.min(DUR, r * 3.6 + 2.9), 0); ring.motionBlur = true; }",
            "var measurements = [];",
            "for (var i = 0; i < entries.length; i += 1) { var entry = entries[i]; var slot = positions[i % positions.length]; var layer = comp.layers.addBoxText([slot.boxW, slot.boxH]); layer.name = 'lyric_word_' + (i + 1) + '_src_' + (entry.source_timecode || '').replace(/[^0-9.]/g, '_'); applyTextStyle(layer, entry.text, slot, i); var rect = layer.sourceRectAtTime(0, false); var anchor = [rect.left + rect.width / 2, rect.top + rect.height / 2]; layer.property('Anchor Point').setValue(anchor); var st = Math.max(0, Math.min(DUR - 1.1, entry.preview_time_seconds || (1 + i * 1.2))); var len = Math.max(1.7, Math.min(3.6, entry.preview_duration_seconds || 2.6)); var driftX = (i % 2 === 0 ? -44 : 44); var driftY = (i % 3 - 1) * 24; var pos = layer.property('Position'); pos.setValueAtTime(Math.max(0, st - 0.18), [slot.x + driftX, slot.y + driftY + 18]); pos.setValueAtTime(st + 0.42, [slot.x, slot.y]); pos.setValueAtTime(Math.min(DUR, st + len), [slot.x - driftX * 0.45, slot.y - driftY - 14]); setEase(pos); var opacity = layer.property('Opacity'); opacity.setValueAtTime(Math.max(0, st - 0.22), 0); opacity.setValueAtTime(st + 0.32, 100); opacity.setValueAtTime(Math.min(DUR, st + len - 0.42), 96); opacity.setValueAtTime(Math.min(DUR, st + len), 0); setEase(opacity); var scale = layer.property('Scale'); scale.setValueAtTime(Math.max(0, st - 0.15), [94, 94]); scale.setValueAtTime(st + 0.45, [100, 100]); scale.setValueAtTime(Math.min(DUR, st + len), [103, 103]); setEase(scale); layer.property('Rotation').setValueAtTime(st, (i % 5 - 2) * 1.8); layer.property('Rotation').setValueAtTime(Math.min(DUR, st + len), (2 - i % 5) * 1.2); layer.inPoint = Math.max(0, st - 0.3); layer.outPoint = Math.min(DUR, st + len + 0.2); layer.motionBlur = true; try { var blur = layer.property('Effects').addProperty('ADBE Gaussian Blur 2'); blur.property('Blurriness').setValueAtTime(Math.max(0, st - 0.18), 8); blur.property('Blurriness').setValueAtTime(st + 0.35, 0); blur.property('Blurriness').setValueAtTime(Math.min(DUR, st + len), 5); } catch (blurErr) {} layer.comment = 'source_timecode=' + (entry.source_timecode || '') + '; comp_time=' + (entry.comp_time_seconds || '') + '; preview_time=' + st.toFixed(2) + '; sourceRect=' + [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)].join(','); measurements.push({name: layer.name, summary: entry.summary || entry.text, source_timecode: entry.source_timecode || '', comp_time_seconds: entry.comp_time_seconds || 0, preview_time_seconds: st, sourceRect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height}, anchorPoint: anchor, position: [slot.x, slot.y], boxSize: [slot.boxW, slot.boxH], motionBlur: true}); }",
            "var audioItem = importFootage(audioPath); if (audioItem) { var aud = comp.layers.add(audioItem); aud.name = 'placeholder_audio_qinghua_rain_pad'; aud.startTime = 0; aud.inPoint = 0; aud.outPoint = DUR; }",
            "if (!measurementReportFile.parent.exists) { measurementReportFile.parent.create(); } measurementReportFile.encoding = 'UTF-8'; measurementReportFile.open('w'); measurementReportFile.write(jsonStringify({composition: compName, duration_seconds: DUR, text_layer_count: measurements.length, measurements: measurements})); measurementReportFile.close();",
            "comp.openInViewer(); app.project.save(saveFile); app.endUndoGroup();",
        ]
    )


def build_shotwright_tools(app_session_id: str) -> list[Tool]:
    """Build session-scoped tools for the Copilot runtime."""

    def _coerce_timeout_seconds(raw_value: object, default: int = 300) -> int:
        if raw_value is None:
            return default
        try:
            return max(30, int(raw_value))
        except (TypeError, ValueError):
            return default

    def _coerce_bool(raw_value: object, default: bool = False) -> bool:
        if raw_value is None:
            return default
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(raw_value)

    def _coerce_python_timeout_seconds(raw_value: object, default: int = 180) -> int:
        if raw_value is None:
            return default
        try:
            return min(900, max(1, int(raw_value)))
        except (TypeError, ValueError):
            return default

    async def _resolve_python_workspace(args: dict, session_doc: dict) -> tuple[Path, dict | None, str | None, str | None]:
        project = None
        project_id = str(args.get("project_id") or session_doc.get("active_project_id") or "").strip() or None
        if project_id:
            project = await pm.get_project(app_session_id, project_id)
            if not project:
                raise ValueError(f"Project {project_id} not found.")

        session_upload_dir = (pm.UPLOAD_DIR / app_session_id).resolve()
        session_export_dir = (pm.EXPORT_DIR / app_session_id).resolve()
        allowed_roots = [session_upload_dir, session_export_dir]
        default_root = session_upload_dir / "_python"

        project_root: Path | None = None
        if project:
            project_root = Path(project["workspace_dir"]).resolve()
            allowed_roots.append(project_root)
            default_root = project_root

        raw_work_dir = str(args.get("work_dir") or "").strip()
        if raw_work_dir:
            requested_work_dir = Path(raw_work_dir)
            if not requested_work_dir.is_absolute():
                requested_work_dir = default_root / requested_work_dir
            work_dir = requested_work_dir.resolve()
        else:
            work_dir = default_root.resolve()

        if not any(_path_is_inside(work_dir, root) for root in allowed_roots):
            allowed_text = ", ".join(str(root) for root in allowed_roots)
            raise ValueError(f"work_dir must stay inside this session workspace. Allowed roots: {allowed_text}")

        work_dir.mkdir(parents=True, exist_ok=True)
        entry_aep_file = str(project.get("entry_aep_file") or project.get("filename") or "").strip() if project else ""
        return work_dir, project, project_id, entry_aep_file or None

    def _resolve_session_workspace_path(
        raw_path: object,
        *,
        project: dict | None = None,
        default_root: Path | None = None,
    ) -> Path:
        normalized = str(raw_path or "").strip()
        if not normalized:
            raise ValueError("script_path is required.")

        session_upload_dir = (pm.UPLOAD_DIR / app_session_id).resolve()
        session_export_dir = (pm.EXPORT_DIR / app_session_id).resolve()
        allowed_roots = [session_upload_dir, session_export_dir]

        project_root: Path | None = None
        if project:
            project_root = Path(project["workspace_dir"]).resolve()
            allowed_roots.append(project_root)

        base_root = default_root or project_root or session_upload_dir
        requested_path = Path(normalized)
        if not requested_path.is_absolute():
            requested_path = base_root / requested_path
        resolved_path = requested_path.resolve()

        if not any(_path_is_inside(resolved_path, root) for root in allowed_roots):
            allowed_text = ", ".join(str(root) for root in allowed_roots)
            raise ValueError(f"script_path must stay inside this session workspace. Allowed roots: {allowed_text}")
        return resolved_path

    async def _create_project_from_script(
        *,
        arguments: dict,
        script_content: str,
        default_description: str,
    ) -> ToolResult:
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")
        if not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project = None
        reused_existing_workspace = False
        candidate_project_ids: list[str] = []

        requested_project_id = str(arguments.get("project_id") or "").strip()
        if requested_project_id:
            candidate_project_ids.append(requested_project_id)

        active_project_id = str(session_doc.get("active_project_id") or "").strip()
        if active_project_id and active_project_id not in candidate_project_ids:
            candidate_project_ids.append(active_project_id)

        for candidate_project_id in candidate_project_ids:
            candidate_project = await pm.get_project(app_session_id, candidate_project_id)
            if _should_reuse_generated_project_workspace(candidate_project):
                project = candidate_project
                reused_existing_workspace = True
                break

        if project is None:
            project = await pm.create_project_workspace(
                app_session_id,
                project_name=arguments.get("project_name"),
                aep_filename=arguments.get("aep_filename"),
                set_active=False,
            )

        await pm.set_active_project(app_session_id, project["_id"])
        result = await nr.run_jsx_script(
            session_doc["container_id"],
            script_content,
            project=project,
            timeout_seconds=_coerce_timeout_seconds(arguments.get("timeout_seconds")),
        )
        refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"])
        if not refreshed_project or not refreshed_project.get("aep_files"):
            fallback_result = None
            if not _coerce_bool(arguments.get("disable_safe_fallback"), False):
                fallback_result = await nr.run_jsx_script(
                    session_doc["container_id"],
                    _build_safe_after_effects_demo_script(),
                    project=project,
                    timeout_seconds=_coerce_timeout_seconds(arguments.get("timeout_seconds")),
                )
                refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"])
                if refreshed_project and refreshed_project.get("aep_files"):
                    result = {
                        **fallback_result,
                        "safe_fallback_used": True,
                        "initial_script_result": result,
                    }

        if not refreshed_project or not refreshed_project.get("aep_files"):
            payload = {
                **result,
                "project_id": project["_id"],
                "workspace_dir": project["workspace_dir"],
                "entry_aep_file": project.get("entry_aep_file"),
                "entry_aep_path": str(Path(project["workspace_dir"]) / project["entry_aep_file"]),
                "reused_existing_workspace": reused_existing_workspace,
            }
            if fallback_result is not None:
                payload["safe_fallback_attempt"] = fallback_result
            return ToolResult(
                text_result_for_llm=json.dumps(payload, ensure_ascii=False),
                result_type="failure",
                error=(
                    "JSX did not save an .aep file into the managed project workspace. "
                    "Reuse this same project_id on the next retry instead of creating another workspace, "
                    "and leave the intended project open or save it to SHOTWRIGHT_PROJECT_FILE so the wrapper can persist it."
                ),
                session_log=arguments.get("description") or default_description,
            )

        await pm.set_active_project(app_session_id, refreshed_project["_id"])
        active_project = await pm.get_project(app_session_id, refreshed_project["_id"]) or refreshed_project
        entry_aep_file = active_project.get("entry_aep_file") or (active_project.get("aep_files") or [None])[0]
        payload = {
            **result,
            "project_id": active_project["_id"],
            "filename": active_project["filename"],
            "origin": active_project.get("origin", "generated"),
            "workspace_dir": active_project["workspace_dir"],
            "entry_aep_file": entry_aep_file,
            "entry_aep_path": str(Path(active_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None,
            "aep_files": active_project.get("aep_files", []),
            "compositions": active_project.get("compositions", []),
            "composition_catalog_updated_at": active_project.get("composition_catalog_updated_at"),
            "reused_existing_workspace": reused_existing_workspace,
        }
        return _tool_success(
            payload,
            arguments.get("description") or default_description,
        )

    async def inspect_workspace(invocation: ToolInvocation) -> ToolResult:
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        container = None
        if session_doc.get("container_id"):
            container = await cm.get_container(session_doc["container_id"])

        projects = await pm.list_projects(app_session_id)
        recent_image_attachments = await _list_session_image_attachments(app_session_id, limit=6)
        payload = {
            "session_id": app_session_id,
            "status": session_doc.get("status"),
            "container": {
                "id": container.get("_id"),
                "status": container.get("status"),
                "docker_id": container.get("docker_id"),
            }
            if container
            else None,
            "active_project_id": session_doc.get("active_project_id"),
            "projects": [
                {
                    "project_id": project["_id"],
                    "filename": project["filename"],
                    "origin": project.get("origin", "uploaded"),
                    "entry_aep_file": project.get("entry_aep_file"),
                    "aep_files": project.get("aep_files", []),
                    "compositions": project.get("compositions", []),
                    "composition_catalog_updated_at": project.get("composition_catalog_updated_at"),
                    "workspace_dir": project["workspace_dir"],
                }
                for project in projects
            ],
            "recent_image_attachments": recent_image_attachments,
            "latest_render_path": session_doc.get("latest_render_path"),
            "latest_stream_url": session_doc.get("latest_stream_url"),
            "render_outputs": nr.list_render_outputs(app_session_id, limit=8),
            "reference_videos": rm.list_reference_videos(app_session_id, limit=8),
            "storyboards": rm.list_storyboards(app_session_id, limit=8),
            "tts_audio": tts_media.list_tts_audio(app_session_id, limit=8),
            "recommended_fonts": SHOTWRIGHT_RECOMMENDED_FONTS,
            "creative_quality_policy": SHOTWRIGHT_CREATIVE_QUALITY_POLICY,
            "subtitle_style_policy": SHOTWRIGHT_SUBTITLE_STYLE_POLICY,
        }
        return _tool_success(payload, "Loaded Shotwright workspace state")

    async def ensure_after_effects_container(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        container_id = session_doc.get("container_id")
        if container_id:
            container = await cm.get_container(container_id)
            if container and container.get("status") == "running":
                return _tool_success(
                    {
                        "container_id": container["_id"],
                        "docker_id": container["docker_id"],
                        "status": container["status"],
                    },
                    "Reused existing running After Effects container",
                )

        created = await cm.create_container(app_session_id, args.get("image"))
        return _tool_success(
            {
                "container_id": created["_id"],
                "docker_id": created["docker_id"],
                "status": created["status"],
                "image": created["image"],
            },
            "Started a new Shotwright After Effects container",
        )

    async def create_after_effects_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        script_content = (args.get("script_content") or "").strip()
        if not script_content:
            return _tool_failure("script_content is required.")

        return await _create_project_from_script(
            arguments=args,
            script_content=script_content,
            default_description="Created managed After Effects project",
        )

    async def create_empty_after_effects_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        project = None
        reused_existing_workspace = False
        candidate_project_ids: list[str] = []

        requested_project_id = str(args.get("project_id") or "").strip()
        if requested_project_id:
            candidate_project_ids.append(requested_project_id)

        active_project_id = str(session_doc.get("active_project_id") or "").strip()
        if active_project_id and active_project_id not in candidate_project_ids:
            candidate_project_ids.append(active_project_id)

        for candidate_project_id in candidate_project_ids:
            candidate_project = await pm.get_project(app_session_id, candidate_project_id)
            if _should_reuse_generated_project_workspace(candidate_project):
                project = candidate_project
                reused_existing_workspace = True
                break

        if project is None:
            project = await pm.create_project_workspace(
                app_session_id,
                project_name=args.get("project_name"),
                aep_filename=args.get("aep_filename"),
                set_active=False,
            )

        bootstrap_template_path = nr._resolve_nexrender_bootstrap_template()
        if not bootstrap_template_path.exists():
            return _tool_failure(f"Bootstrap template not found at {bootstrap_template_path}")

        entry_aep_file = project.get("entry_aep_file") or project.get("filename")
        if not entry_aep_file:
            return _tool_failure("Managed project is missing entry_aep_file.")

        target_aep_path = Path(project["workspace_dir"]) / entry_aep_file
        if not target_aep_path.exists():
            target_aep_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bootstrap_template_path, target_aep_path)

        metadata_path = Path(project["workspace_dir"]) / pm.PROJECT_METADATA_FILENAME
        metadata_path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "compositions": [{"name": nr.BOOTSTRAP_TEMPLATE_COMPOSITION}],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        await pm.set_active_project(app_session_id, project["_id"])
        refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"]) or project
        refreshed_entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]

        payload = {
            "success": True,
            "bootstrap_template_path": str(bootstrap_template_path),
            "project_id": refreshed_project["_id"],
            "filename": refreshed_project["filename"],
            "origin": refreshed_project.get("origin", "generated"),
            "workspace_dir": refreshed_project["workspace_dir"],
            "entry_aep_file": refreshed_entry_aep_file,
            "entry_aep_path": (
                str(Path(refreshed_project["workspace_dir"]) / refreshed_entry_aep_file)
                if refreshed_entry_aep_file
                else None
            ),
            "aep_files": refreshed_project.get("aep_files", []),
            "compositions": refreshed_project.get("compositions", []),
            "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            "reused_existing_workspace": reused_existing_workspace,
        }
        return _tool_success(
            payload,
            args.get("description") or "Created empty After Effects project",
        )

    async def list_uploaded_projects(invocation: ToolInvocation) -> ToolResult:
        projects = await pm.list_projects(app_session_id)
        payload = {
            "projects": [
                {
                    "project_id": project["_id"],
                    "filename": project["filename"],
                    "origin": project.get("origin", "uploaded"),
                    "entry_aep_file": project.get("entry_aep_file"),
                    "aep_files": project.get("aep_files", []),
                    "compositions": project.get("compositions", []),
                    "composition_catalog_updated_at": project.get("composition_catalog_updated_at"),
                    "workspace_dir": project["workspace_dir"],
                }
                for project in projects
            ]
        }
        return _tool_success(payload, "Listed Shotwright session projects")

    async def select_active_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        project_id = args.get("project_id")
        if not project_id:
            return _tool_failure("project_id is required.")

        project = await pm.get_project(app_session_id, project_id)
        if not project:
            return _tool_failure(f"Project {project_id} not found.")

        await pm.set_active_project(app_session_id, project_id)
        return _tool_success(
            {
                "project_id": project_id,
                "filename": project["filename"],
                "origin": project.get("origin", "uploaded"),
                "entry_aep_file": project.get("entry_aep_file"),
                "aep_files": project.get("aep_files", []),
                "compositions": project.get("compositions", []),
                "composition_catalog_updated_at": project.get("composition_catalog_updated_at"),
            },
            f"Selected project {project['filename']} as active",
        )

    async def stage_reference_images(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        if not project_id:
            return _tool_failure("No active project is selected. Create or select a project first.")

        project = await pm.get_project(app_session_id, project_id)
        if not project:
            return _tool_failure(f"Project {project_id} not found.")

        staged_images = await _stage_session_image_attachments(
            app_session_id,
            project,
            latest_only=_coerce_bool(args.get("latest_only"), default=True),
            asset_name=args.get("asset_name"),
        )
        if not staged_images:
            return _tool_failure(
                "No session image attachments are available. Send an inline image first or provide a project-relative reference asset path."
            )

        refreshed_project = await pm.refresh_project_files(app_session_id, project_id) or project
        entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]
        payload = {
            "project_id": project_id,
            "workspace_dir": refreshed_project["workspace_dir"],
            "entry_aep_file": entry_aep_file,
            "entry_aep_path": str(Path(refreshed_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None,
            "aep_files": refreshed_project.get("aep_files", []),
            "compositions": refreshed_project.get("compositions", []),
            "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            "staged_images": staged_images,
            "default_reference_asset_path": staged_images[0]["project_asset_path"],
            "default_reference_relative_path": staged_images[0]["project_relative_path"],
        }
        return _tool_success(payload, args.get("description") or "Staged reference images into the active project")

    async def create_reference_composition(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")
        if not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        if not project_id:
            return _tool_failure("No active project is selected. Create or select a project first.")

        project = await pm.get_project(app_session_id, project_id)
        if not project:
            return _tool_failure(f"Project {project_id} not found.")

        raw_reference_path = str(args.get("reference_asset_path") or "").strip()
        if raw_reference_path:
            source_path = Path(raw_reference_path)
            if not source_path.is_absolute():
                source_path = Path(project["workspace_dir"]) / raw_reference_path
            try:
                reference_asset = _copy_asset_into_project(
                    project,
                    source_path,
                    display_name=source_path.name,
                    asset_name=args.get("asset_name"),
                )
            except FileNotFoundError as exc:
                return _tool_failure(str(exc))
        else:
            staged_images = await _stage_session_image_attachments(
                app_session_id,
                project,
                latest_only=True,
                asset_name=args.get("asset_name"),
            )
            if not staged_images:
                return _tool_failure(
                    "No session image attachments are available. Send an inline image first or call stage_reference_images."
                )
            reference_asset = staged_images[0]

        composition_name = str(args.get("composition_name") or "Main").strip() or "Main"
        width = max(16, int(args.get("width") or 1920))
        height = max(16, int(args.get("height") or 1080))
        duration_seconds = max(1.0, float(args.get("duration_seconds") or 10.0))
        frame_rate = max(1.0, float(args.get("frame_rate") or 30.0))
        fit_mode = str(args.get("fit_mode") or "cover").strip().lower() or "cover"
        reset_existing = _coerce_bool(args.get("reset_existing"), default=False)

        result = await nr.run_jsx_script(
            session_doc["container_id"],
            _build_reference_composition_jsx(
                reference_asset_path=reference_asset["project_asset_path"],
                composition_name=composition_name,
                width=width,
                height=height,
                duration_seconds=duration_seconds,
                frame_rate=frame_rate,
                fit_mode=fit_mode,
                reset_existing=reset_existing,
            ),
            project=project,
            timeout_seconds=_coerce_timeout_seconds(args.get("timeout_seconds")),
        )

        await pm.set_active_project(app_session_id, project_id)
        refreshed_project = await pm.refresh_project_files(app_session_id, project_id) or project
        entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]
        payload = {
            **result,
            "project_id": project_id,
            "workspace_dir": refreshed_project["workspace_dir"],
            "entry_aep_file": entry_aep_file,
            "entry_aep_path": str(Path(refreshed_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None,
            "aep_files": refreshed_project.get("aep_files", []),
            "compositions": refreshed_project.get("compositions", []),
            "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            "reference_asset_path": reference_asset["project_asset_path"],
            "reference_relative_path": reference_asset["project_relative_path"],
            "composition_name": composition_name,
            "duration_seconds": duration_seconds,
            "width": width,
            "height": height,
            "frame_rate": frame_rate,
        }

        result_type = "success" if result.get("success", result.get("exit_code") == 0) else "failure"
        return ToolResult(
            text_result_for_llm=json.dumps(payload, ensure_ascii=False),
            result_type=result_type,
            error=result.get("output") if result_type == "failure" else None,
            session_log=args.get("description") or f"Created or updated composition {composition_name}",
        )

    async def create_lyrics_mv_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        container_id = str(session_doc.get("container_id") or "").strip()
        if container_id:
            container_doc = await cm.get_container(container_id)
            if not container_doc or container_doc.get("status") != "running":
                container_id = ""
        if not container_id:
            created_container = await cm.create_container(app_session_id, args.get("image"))
            container_id = created_container["_id"]

        project_id = str(args.get("project_id") or session_doc.get("active_project_id") or "").strip()
        project = await pm.get_project(app_session_id, project_id) if project_id else None
        if project_id and not project:
            return _tool_failure(f"Project {project_id} not found.")
        if project is None:
            project = await pm.create_project_workspace(
                app_session_id,
                project_name=args.get("project_name") or "lyrics_mv_preview",
                aep_filename=args.get("aep_filename") or "lyrics_mv_preview.aep",
                set_active=False,
            )
            project_id = project["_id"]

        project_root = Path(project["workspace_dir"]).resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        entry_aep_file = str(project.get("entry_aep_file") or project.get("filename") or "lyrics_mv_preview.aep").strip()
        target_aep_path = project_root / entry_aep_file
        if not target_aep_path.exists():
            bootstrap_template_path = nr._resolve_nexrender_bootstrap_template()
            if not bootstrap_template_path.exists():
                return _tool_failure(f"Bootstrap template not found at {bootstrap_template_path}")
            target_aep_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bootstrap_template_path, target_aep_path)

        def resolve_project_path(raw_path: object, default_path: Path, *, label: str) -> Path:
            normalized = str(raw_path or "").strip()
            requested_path = Path(normalized) if normalized else default_path
            if not requested_path.is_absolute():
                requested_path = project_root / requested_path
            resolved_path = requested_path.resolve()
            if not _path_is_inside(resolved_path, project_root):
                raise ValueError(f"{label} must stay inside the active project workspace.")
            return resolved_path

        source_start_timecode = str(args.get("source_start_timecode") or "02:07.00").strip() or "02:07.00"
        duration_seconds = max(40.0, min(55.0, float(args.get("duration_seconds") or 45.0)))
        frame_rate = max(1.0, float(args.get("frame_rate") or 30.0))
        width = max(320, int(args.get("width") or 1080))
        height = max(320, int(args.get("height") or 1920))
        composition_name = str(args.get("composition_name") or "Main").strip() or "Main"

        try:
            mapping_path = resolve_project_path(
                args.get("mapping_path"),
                project_root / "assets" / "data" / "lyric_mapping.json",
                label="mapping_path",
            )
            audio_path = resolve_project_path(
                args.get("audio_path"),
                project_root / "assets" / "audio" / "placeholder_audio_qinghua_rain_pad.wav",
                label="audio_path",
            )
            script_path = resolve_project_path(
                args.get("script_path"),
                project_root / "scripts" / "qinghua_mv_preview.jsx",
                label="script_path",
            )
            measurement_report_path = resolve_project_path(
                args.get("measurement_report_path"),
                project_root / "reports" / "qinghua_mv_measurements.json",
                label="measurement_report_path",
            )
        except ValueError as exc:
            return _tool_failure(str(exc))

        mapping_created_from_lrc = False
        if mapping_path.is_file():
            mapping_entries = _load_lyrics_mapping(mapping_path, source_start_timecode=source_start_timecode)
        else:
            lyrics_lrc = str(args.get("lyrics_lrc") or "").strip() or await _find_recent_lrc_text(app_session_id)
            mapping_payload = _build_lyrics_mapping_from_lrc(
                lyrics_lrc,
                source_start_timecode=source_start_timecode,
                duration_seconds=duration_seconds,
            )
            mapping_entries = _load_lyrics_mapping_from_payload(mapping_payload, source_start_timecode=source_start_timecode)
            if mapping_entries:
                mapping_path.parent.mkdir(parents=True, exist_ok=True)
                mapping_path.write_text(json.dumps(mapping_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                mapping_created_from_lrc = True

        if not mapping_entries:
            return _tool_failure(
                "No lyric mapping is available. Provide lyrics_lrc with [mm:ss.xx] time tags or create assets/data/lyric_mapping.json first."
            )

        selected_entries = _select_lyrics_mv_preview_entries(mapping_entries, duration_seconds)
        if not selected_entries:
            return _tool_failure("Lyric mapping did not contain usable text entries.")

        placeholder_audio_note = ""
        if not audio_path.is_file():
            _write_lyrics_mv_placeholder_audio(audio_path, duration_seconds=duration_seconds)
            placeholder_audio_note = (
                "Generated an original placeholder atmosphere track. No copyrighted song audio was downloaded or embedded."
            )

        image_dir = project_root / "assets" / "images"
        image_paths = {
            "porcelain": image_dir / "porcelain_rain_wash.png",
            "crack": image_dir / "ceramic_cracks_overlay.png",
            "ink": image_dir / "ink_diffusion_overlay.png",
        }
        script_path.parent.mkdir(parents=True, exist_ok=True)
        measurement_report_path.parent.mkdir(parents=True, exist_ok=True)
        script_content = _build_lyrics_mv_project_jsx(
            project_root=project_root,
            aep_path=target_aep_path,
            mapping_entries=selected_entries,
            audio_path=audio_path,
            image_paths=image_paths,
            composition_name=composition_name,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            frame_rate=frame_rate,
            measurement_report_path=measurement_report_path,
        )
        script_path.write_text(script_content, encoding="utf-8")

        await pm.set_active_project(app_session_id, project_id)
        result = await nr.run_jsx_script(
            container_id,
            script_content,
            project=project,
            timeout_seconds=_coerce_timeout_seconds(args.get("timeout_seconds"), default=240),
        )
        refreshed_project = await pm.refresh_project_files(app_session_id, project_id) or project
        entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]
        payload = {
            **result,
            "project_id": project_id,
            "workspace_dir": refreshed_project["workspace_dir"],
            "entry_aep_file": entry_aep_file,
            "entry_aep_path": str(Path(refreshed_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None,
            "aep_files": refreshed_project.get("aep_files", []),
            "compositions": refreshed_project.get("compositions", []),
            "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            "composition_name": composition_name,
            "duration_seconds": duration_seconds,
            "width": width,
            "height": height,
            "frame_rate": frame_rate,
            "script_path": str(script_path),
            "script_relative_path": script_path.relative_to(project_root).as_posix(),
            "script_source": "generated_backend_template",
            "mapping_path": str(mapping_path),
            "mapping_created_from_lrc": mapping_created_from_lrc,
            "selected_lyric_count": len(selected_entries),
            "measurement_report_path": str(measurement_report_path),
            "audio_path": str(audio_path),
            "placeholder_audio_note": placeholder_audio_note,
            "next_steps": [
                "Render this project with render_after_effects_project.",
                "Generate a storyboard from the render and review text layout before finalizing.",
            ],
        }
        result_type = "success" if result.get("success", result.get("exit_code") == 0) else "failure"
        return ToolResult(
            text_result_for_llm=_serialize_tool_payload(payload),
            result_type=result_type,
            error=result.get("output") if result_type == "failure" else None,
            session_log=args.get("description") or "Created a lyric MV After Effects project with measured paragraph text",
        )

    async def generate_storyboard_from_reference_video(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        try:
            payload = rm.generate_storyboard(
                app_session_id,
                reference_video_path=args.get("reference_video_path"),
                output_name=args.get("output_name"),
                start_seconds=float(args.get("start_seconds")) if args.get("start_seconds") is not None else None,
                clip_duration_seconds=(
                    float(args.get("clip_duration_seconds")) if args.get("clip_duration_seconds") is not None else None
                ),
                interval_seconds=float(args.get("interval_seconds")) if args.get("interval_seconds") is not None else None,
                columns=int(args.get("columns")) if args.get("columns") is not None else None,
                width=int(args.get("width")) if args.get("width") is not None else None,
                crop=args.get("crop"),
            )
        except rm.ReferenceMediaUnavailableError as exc:
            return _tool_failure(str(exc), error=str(exc))
        except (FileNotFoundError, TypeError, ValueError) as exc:
            return _tool_failure(str(exc))

        return _tool_success(
            payload,
            args.get("description") or "Generated storyboard from the reference video",
        )

    async def generate_tts_audio(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        text = str(args.get("text") or "").strip()
        if not text:
            return _tool_failure("text is required.")

        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        project = None
        project_id = str(args.get("project_id") or session_doc.get("active_project_id") or "").strip()
        copy_to_project = _coerce_bool(args.get("copy_to_project"), bool(project_id))
        if copy_to_project and project_id:
            project = await pm.get_project(app_session_id, project_id)
            if not project:
                return _tool_failure(f"Project {project_id} not found.")

        python_executable: Path | None = None
        dependency_runtime: dict | None = None
        dependency_env: dict[str, str] = {}
        if _tts_provider_needs_python_runtime(args.get("provider")):
            python_executable, dependency_runtime, dependency_env, dependency_error = await _ensure_python_tool_runtime()
            if dependency_error:
                payload = {
                    "success": False,
                    "python_executable": str(python_executable),
                    "python_dependency_runtime": dependency_runtime,
                    "error": dependency_error,
                }
                return ToolResult(
                    text_result_for_llm=_serialize_tool_payload(payload),
                    result_type="failure",
                    error=dependency_error,
                    session_log=args.get("description") or "Prepared TTS Python provider runtime",
                )

        admin_doc = await get_admin_collection().find_one({"_id": "settings"}) or {}
        try:
            payload = await asyncio.to_thread(
                tts_media.generate_tts_audio,
                app_session_id,
                text=text,
                provider=args.get("provider"),
                voice=str(args.get("voice") or ""),
                model=str(args.get("model") or ""),
                language=str(args.get("language") or ""),
                audio_format=str(args.get("format") or args.get("audio_format") or ""),
                output_name=args.get("output_name"),
                instructions=str(args.get("instructions") or ""),
                speed=float(args["speed"]) if args.get("speed") is not None else None,
                rate=str(args.get("rate") or ""),
                pitch=str(args.get("pitch") or ""),
                volume=str(args.get("volume") or ""),
                base_url=str(args.get("base_url") or ""),
                openai_api_key=resolve_openai_api_key(admin_doc),
                python_executable=python_executable,
                python_env=dependency_env,
                timeout_seconds=_coerce_python_timeout_seconds(args.get("timeout_seconds"), default=180),
            )
        except (tts_media.TTSProviderError, FileNotFoundError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
            return _tool_failure(str(exc).strip() or exc.__class__.__name__)

        if project:
            staged_audio = _copy_asset_into_project(
                project,
                Path(payload["file_path"]),
                display_name=payload.get("filename"),
                asset_name=args.get("asset_name") or payload.get("filename"),
                target_directory=_AUDIO_ASSET_DIRECTORY,
            )
            refreshed_project = await pm.refresh_project_files(app_session_id, project_id) or project
            payload.update(
                {
                    "project_id": project_id,
                    "project_workspace_dir": project.get("workspace_dir"),
                    "project_audio_path": staged_audio["project_asset_path"],
                    "project_relative_path": staged_audio["project_relative_path"],
                    "project": {
                        "_id": refreshed_project["_id"],
                        "filename": refreshed_project["filename"],
                        "workspace_dir": refreshed_project["workspace_dir"],
                        "entry_aep_file": refreshed_project.get("entry_aep_file"),
                        "aep_files": refreshed_project.get("aep_files", []),
                        "compositions": refreshed_project.get("compositions", []),
                        "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
                    },
                }
            )
            await publish_context_refresh(
                app_session_id,
                "tts_audio.staged",
                project_id=project_id,
                tts_audio_path=payload["shared_relative_path"],
                project_audio_path=staged_audio["project_relative_path"],
            )
        if dependency_runtime is not None:
            payload["python_dependency_runtime"] = dependency_runtime

        return _tool_success(
            payload,
            args.get("description") or "Generated TTS narration audio",
        )

    async def run_python_code(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        script_content = str(args.get("script_content") or "").strip()
        if not script_content:
            return _tool_failure("script_content is required.")

        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        try:
            work_dir, project, project_id, entry_aep_file = await _resolve_python_workspace(args, session_doc)
        except ValueError as exc:
            return _tool_failure(str(exc))

        script_dir = work_dir / _PYTHON_TOOL_SCRIPT_DIRECTORY
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / f"script-{uuid4().hex}.py"
        script_path.write_text(script_content, encoding="utf-8")

        timeout_seconds = _coerce_python_timeout_seconds(args.get("timeout_seconds"))
        before_files = _snapshot_workspace_files(work_dir)
        started_at = datetime.now(timezone.utc)
        python_executable, dependency_runtime, dependency_env, dependency_error = await _ensure_python_tool_runtime()
        if dependency_error:
            payload = {
                "success": False,
                "exit_code": None,
                "timed_out": False,
                "timeout_seconds": timeout_seconds,
                "elapsed_ms": int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
                "python_executable": str(python_executable),
                "script_path": str(script_path),
                "work_dir": str(work_dir),
                "project_id": project_id,
                "project_workspace_dir": project.get("workspace_dir") if project else None,
                "python_dependency_runtime": dependency_runtime,
                "error": dependency_error,
            }
            return ToolResult(
                text_result_for_llm=_serialize_tool_payload(payload),
                result_type="failure",
                error=dependency_error,
                session_log=args.get("description") or "Prepared Python media processing dependencies",
            )

        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "SHOTWRIGHT_SESSION_ID": app_session_id,
                "SHOTWRIGHT_WORK_DIR": str(work_dir),
                "SHOTWRIGHT_UPLOAD_DIR": str(pm.UPLOAD_DIR),
                "SHOTWRIGHT_EXPORT_DIR": str(pm.EXPORT_DIR),
            }
        )
        env.update(dependency_env)
        if project and project_id:
            env["SHOTWRIGHT_PROJECT_ID"] = project_id
            env["SHOTWRIGHT_PROJECT_ROOT"] = str(Path(project["workspace_dir"]))
            if entry_aep_file:
                env["SHOTWRIGHT_PROJECT_FILE"] = str(Path(project["workspace_dir"]) / entry_aep_file)

        try:
            completed = subprocess.run(
                [str(python_executable), str(script_path)],
                cwd=str(work_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            timed_out = False
            exit_code = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or f"Python execution timed out after {timeout_seconds} seconds."

        elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        after_files = _snapshot_workspace_files(work_dir)
        changed_files = _diff_workspace_files(before_files, after_files)
        if project and project_id:
            await pm.refresh_project_files(app_session_id, project_id)

        stdout = _truncate_text(_redact_sensitive_text(str(stdout)))
        stderr = _truncate_text(_redact_sensitive_text(str(stderr)))
        payload = {
            "success": exit_code == 0 and not timed_out,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "timeout_seconds": timeout_seconds,
            "elapsed_ms": elapsed_ms,
            "python_executable": str(python_executable),
            "python_dependency_runtime": dependency_runtime,
            "script_path": str(script_path),
            "work_dir": str(work_dir),
            "project_id": project_id,
            "project_workspace_dir": project.get("workspace_dir") if project else None,
            "stdout": stdout,
            "stderr": stderr,
            "created_or_modified_files": changed_files,
            "created_or_modified_files_truncated": len(changed_files) >= _PYTHON_TOOL_FILE_LIST_LIMIT,
        }
        result_type = "success" if payload["success"] else "failure"
        return ToolResult(
            text_result_for_llm=_serialize_tool_payload(payload),
            result_type=result_type,
            error=(stderr or stdout) if result_type == "failure" else None,
            session_log=args.get("description") or "Ran Python media processing code",
        )

    async def run_after_effects_jsx(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        inline_script_content = str(args.get("script_content") or "").strip()
        raw_script_path = str(args.get("script_path") or "").strip()
        if not inline_script_content and not raw_script_path:
            return _tool_failure("script_content or script_path is required.")

        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc or not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        project = None
        if project_id:
            project = await pm.get_project(app_session_id, project_id)
            if not project:
                return _tool_failure(f"Project {project_id} not found.")

        script_source = "inline"
        script_path: Path | None = None
        script_content = inline_script_content
        if raw_script_path:
            try:
                script_path = _resolve_session_workspace_path(raw_script_path, project=project)
            except ValueError as exc:
                return _tool_failure(str(exc))
            if script_path.suffix.lower() not in _AFTER_EFFECTS_SCRIPT_EXTENSIONS:
                return _tool_failure(
                    "script_path must point to an After Effects script file with extension .jsx, .jsxinc, or .js."
                )
            if not script_path.is_file():
                return _tool_failure(f"script_path not found: {script_path}")
            try:
                script_content = script_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                script_content = script_path.read_text(encoding="utf-8-sig")
            script_source = "script_path"
            if not script_content.strip():
                return _tool_failure(f"script_path is empty: {script_path}")

        result = await nr.run_jsx_script(
            session_doc["container_id"],
            script_content,
            project=project,
            timeout_seconds=_coerce_timeout_seconds(args.get("timeout_seconds")),
        )
        payload = dict(result)
        payload["script_source"] = script_source
        if script_path is not None:
            payload["script_path"] = str(script_path)
        if project:
            refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"])
            if refreshed_project:
                entry_aep_file = refreshed_project.get("entry_aep_file") or (refreshed_project.get("aep_files") or [None])[0]
                payload["project_id"] = refreshed_project["_id"]
                payload["workspace_dir"] = refreshed_project["workspace_dir"]
                payload["entry_aep_file"] = entry_aep_file
                payload["entry_aep_path"] = (
                    str(Path(refreshed_project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None
                )
                payload["aep_files"] = refreshed_project.get("aep_files", [])
                payload["compositions"] = refreshed_project.get("compositions", [])
                payload["composition_catalog_updated_at"] = refreshed_project.get("composition_catalog_updated_at")

        result_type = "success" if result.get("success", result.get("exit_code") == 0) else "failure"
        return ToolResult(
            text_result_for_llm=json.dumps(payload, ensure_ascii=False),
            result_type=result_type,
            error=result.get("output") if result_type == "failure" else None,
            session_log=args.get("description") or "Executed After Effects JSX script",
        )

    def _generated_project_looks_renderable(project: dict, composition_name: str) -> tuple[bool, str]:
        if str(project.get("origin") or "").strip().lower() != "generated":
            return True, ""

        compositions = project.get("compositions") if isinstance(project.get("compositions"), list) else []
        requested = (composition_name or "Main").strip() or "Main"
        matching = [
            item
            for item in compositions
            if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == requested.lower()
        ]
        if not matching:
            return (
                False,
                f"Generated project has no recorded composition named {requested!r}. "
                "Run run_after_effects_jsx or create_after_effects_project to create and save the requested composition before rendering.",
            )

        comp = matching[0]
        width = comp.get("width")
        height = comp.get("height")
        duration_seconds = comp.get("duration_seconds")
        layer_count = comp.get("layer_count")
        if not width or not height or not duration_seconds or not layer_count:
            return (
                False,
                f"Generated project composition {requested!r} is still an empty or unverified workspace shell "
                f"(width={width}, height={height}, duration_seconds={duration_seconds}, layer_count={layer_count}). "
                "Do not render yet. First run run_after_effects_jsx against this same project_id to create the real comp, "
                "add the requested layers, save the AEP, and verify inspect_workspace reports dimensions and a nonzero layer_count.",
            )
        return True, ""

    async def render_after_effects_project(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")
        if not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        if not project_id:
            return _tool_failure("No active project is selected. Use list_uploaded_projects and select_active_project first.")

        project = await pm.get_project(app_session_id, project_id)
        if not project:
            return _tool_failure(f"Project {project_id} not found.")

        await pm.set_active_project(app_session_id, project_id)
        composition_name = str(args.get("composition") or "Main").strip() or "Main"
        renderable, renderable_error = _generated_project_looks_renderable(project, composition_name)
        if not renderable:
            return _tool_failure(renderable_error)

        try:
            render = await nr.render_project(
                session_id=app_session_id,
                project_id=project_id,
                container_db_id=session_doc["container_id"],
                aep_relative_path=args.get("aep_file"),
                composition=composition_name,
                output_name=args.get("output_name"),
                patch_script=args.get("patch_script"),
            )
        except (FileNotFoundError, ValueError) as exc:
            return _tool_failure(str(exc).strip() or exc.__class__.__name__)

        if not render["success"]:
            failure_payload = {
                **render,
                "project_id": project_id,
                "requested_composition": composition_name,
                "failure_details": nr.format_render_failure_details(
                    render,
                    composition=composition_name,
                ),
            }
            return ToolResult(
                text_result_for_llm=_serialize_tool_payload(failure_payload),
                result_type="failure",
                error=failure_payload["failure_details"],
                session_log="After Effects render failed",
            )

        stream_result = await generate_hls(render["output_path"], render["stream_id"])
        latest_stream_url = stream_result.get("playlist_url") if stream_result.get("success") else None
        render_output = nr.record_render_output(
            session_id=app_session_id,
            project_id=project_id,
            output_path=render["output_path"],
            composition=composition_name,
            aep_path=render["aep_path"],
            work_dir=render.get("work_dir"),
            stdout_path=render.get("stdout_path"),
            stderr_path=render.get("stderr_path"),
            stream_id=render.get("stream_id"),
            playlist_url=latest_stream_url,
            project_workspace_dir=project.get("workspace_dir"),
        )
        refreshed_project = await pm.refresh_project_files(app_session_id, project_id) or project
        await session_col.update_one(
            {"_id": app_session_id},
            {
                "$set": {
                    "active_project_id": project_id,
                    "latest_render_path": render["output_path"],
                    "latest_stream_id": render["stream_id"],
                    "latest_stream_url": latest_stream_url,
                }
            },
        )
        await publish_session_updated(app_session_id)
        await publish_context_refresh(
            app_session_id,
            "render.completed",
            project_id=project_id,
            composition=composition_name,
            render_path=render["output_path"],
            render_id=render_output["id"],
        )

        payload = {
            **render,
            "playlist_url": latest_stream_url,
            "stream_ready": bool(latest_stream_url),
            "render_output": render_output,
            "project_id": project_id,
            "active_project_id": project_id,
            "project": {
                "_id": refreshed_project["_id"],
                "filename": refreshed_project["filename"],
                "workspace_dir": refreshed_project["workspace_dir"],
                "entry_aep_file": refreshed_project.get("entry_aep_file"),
                "aep_files": refreshed_project.get("aep_files", []),
                "compositions": refreshed_project.get("compositions", []),
                "composition_catalog_updated_at": refreshed_project.get("composition_catalog_updated_at"),
            },
        }
        return _tool_success(payload, f"Rendered project {project_id}")

    async def export_project_archive(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        project_id = args.get("project_id") or session_doc.get("active_project_id")
        if not project_id:
            return _tool_failure("No active project is selected.")

        archive = await pm.export_project(app_session_id, project_id)
        if not archive:
            return _tool_failure(f"Project {project_id} could not be exported.")

        return _tool_success(
            {
                "project_id": project_id,
                "archive_path": str(archive),
                "download_url": f"/api/projects/{app_session_id}/{project_id}/archive",
            },
            f"Exported project {project_id} as zip archive",
        )

    async def stop_after_effects_container(invocation: ToolInvocation) -> ToolResult:
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc or not session_doc.get("container_id"):
            return _tool_failure("No container is attached to this session.")

        stopped = await cm.stop_container(session_doc["container_id"])
        if not stopped:
            return _tool_failure("Container could not be stopped.")

        return _tool_success(
            {
                "container_id": stopped["_id"],
                "status": stopped["status"],
            },
            "Stopped After Effects container",
        )

    return [
        Tool(
            name="inspect_workspace",
            description="Read the current Shotwright session state, recent image attachments, uploaded reference videos, generated storyboards, container status, uploaded projects, and latest render info.",
            handler=inspect_workspace,
            parameters={"type": "object", "properties": {}},
            skip_permission=True,
        ),
        Tool(
            name="ensure_after_effects_container",
            description="Start an After Effects container for the current session if one is not already running.",
            handler=ensure_after_effects_container,
            parameters={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Optional Shotwright image override",
                    }
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="create_after_effects_project",
            description=(
                "Create a managed Shotwright project workspace, or reuse the current empty generated workspace after a failed bootstrap, "
                "then run an After Effects JSX script to save an .aep into it and keep that project active for later render/export steps."
            ),
            handler=create_after_effects_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional generated project identifier to reuse after an earlier bootstrap failed before saving an .aep",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Human-readable project name used for the default .aep file name",
                    },
                    "aep_filename": {
                        "type": "string",
                        "description": "Optional .aep filename to save inside the managed workspace",
                    },
                    "script_content": {
                        "type": "string",
                        "description": (
                            "Complete JSX script source. Use the current open project, avoid app.newProject() in the warmed host, and save to SHOTWRIGHT_PROJECT_FILE only when you need an explicit path."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the creation step",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution",
                    },
                },
                "required": ["script_content"],
            },
            skip_permission=True,
        ),
        Tool(
            name="create_empty_after_effects_project",
            description=(
                "Create a blank managed Shotwright .aep/workspace shell, or reuse the current empty generated workspace after a failed bootstrap. "
                "This does not create the user's requested render-ready composition; follow it with run_after_effects_jsx before rendering."
            ),
            handler=create_empty_after_effects_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional generated project identifier to reuse after an earlier bootstrap failed before saving an .aep",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Human-readable project name used for the default .aep file name",
                    },
                    "aep_filename": {
                        "type": "string",
                        "description": "Optional .aep filename to save inside the managed workspace",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the creation step",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="list_uploaded_projects",
            description="List all managed or uploaded Shotwright session projects, including discovered .aep files.",
            handler=list_uploaded_projects,
            parameters={"type": "object", "properties": {}},
            skip_permission=True,
        ),
        Tool(
            name="select_active_project",
            description="Mark one uploaded project as the active project for subsequent After Effects actions.",
            handler=select_active_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project identifier returned by list_uploaded_projects",
                    }
                },
                "required": ["project_id"],
            },
            skip_permission=True,
        ),
        Tool(
            name="stage_reference_images",
            description="Copy recent inline image attachments from the session transcript into the active project workspace and return stable project asset paths.",
            handler=stage_reference_images,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    },
                    "latest_only": {
                        "type": "boolean",
                        "description": "When true, stage only the most recent session image attachment",
                    },
                    "asset_name": {
                        "type": "string",
                        "description": "Optional stable file name to use inside the project workspace",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the asset staging step",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="generate_storyboard_from_reference_video",
            description="Generate a storyboard contact sheet from a session-local video clip, including uploaded reference videos and rendered mp4 exports, using ffmpeg sampling parameters without shell fallback.",
            handler=generate_storyboard_from_reference_video,
            parameters={
                "type": "object",
                "properties": {
                    "reference_video_path": {
                        "type": "string",
                        "description": "Optional shared-relative or absolute path to a session-local video clip, including uploaded reference videos and latest_render_path-style export paths; defaults to the newest uploaded reference video",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Optional jpg file name for the generated storyboard image",
                    },
                    "start_seconds": {
                        "type": "number",
                        "description": "Optional ffmpeg -ss style clip start in seconds",
                    },
                    "clip_duration_seconds": {
                        "type": "number",
                        "description": "Optional ffmpeg -t style clip duration in seconds",
                    },
                    "interval_seconds": {
                        "type": "number",
                        "description": "Frame sampling interval in seconds; lower values create denser storyboards",
                    },
                    "columns": {
                        "type": "integer",
                        "description": "Storyboard grid column count",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Per-frame tile width in pixels before tiling",
                    },
                    "crop": {
                        "type": "string",
                        "description": "Optional crop box to inspect local motion, formatted as x,y,width,height or x:y:width:height in pixels or percentages like 25%,10%,40%,35%.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the storyboard generation step",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="generate_tts_audio",
            description=(
                "Generate session-scoped narration or voiceover audio from text for later After Effects import. "
                "Prefer Edge TTS by default. Providers include edge, auto, OpenAI-compatible speech APIs, Azure Speech, ElevenLabs, and Windows SAPI; "
                "credentials are read from admin/environment settings and are never passed in tool arguments. "
                "When copy_to_project is true or an active project exists, the generated audio is staged under assets/audio "
                "and can be imported in JSX with ImportOptions(new File(project_audio_path))."
            ),
            handler=generate_tts_audio,
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Narration text to synthesize.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "TTS provider: edge is preferred; also supports auto, openai/openai_compatible, azure, elevenlabs, or windows_sapi.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Provider-specific voice name or voice id.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Provider-specific TTS model name when supported.",
                    },
                    "language": {
                        "type": "string",
                        "description": "Optional language hint such as zh-CN or en-US.",
                    },
                    "format": {
                        "type": "string",
                        "description": "Output audio format: mp3, wav, aac, opus, flac, or pcm. Defaults to mp3.",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Optional output file name.",
                    },
                    "asset_name": {
                        "type": "string",
                        "description": "Optional file name when copying the audio into the active project assets/audio folder.",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project when copy_to_project is enabled.",
                    },
                    "copy_to_project": {
                        "type": "boolean",
                        "description": "When true, copy the generated audio into the project workspace for AE import. Defaults to true when a project is active.",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Optional provider-specific style or delivery instructions.",
                    },
                    "speed": {
                        "type": "number",
                        "description": "Optional OpenAI-compatible speed value when supported.",
                    },
                    "rate": {
                        "type": "string",
                        "description": "Optional provider-specific rate, e.g. +8% for Edge TTS or 2 for Windows SAPI.",
                    },
                    "pitch": {
                        "type": "string",
                        "description": "Optional Edge TTS pitch value, e.g. +5Hz.",
                    },
                    "volume": {
                        "type": "string",
                        "description": "Optional Edge TTS volume value, e.g. +0%.",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Optional OpenAI-compatible API base URL; API keys still come from admin/environment settings.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the TTS generation step.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional provider timeout in seconds, from 1 to 900. Defaults to 180.",
                    },
                },
                "required": ["text"],
            },
            skip_permission=True,
        ),
        Tool(
            name="run_python_code",
            description=(
                "Execute Python 3.13 code inside the Shotwright session workspace for CPU-only media analysis, "
                "asset generation, audio/video preprocessing, data extraction, and AIGC helper workflows. "
                "The runtime includes numpy, pillow, opencv-python, moviepy, librosa, soundfile, onnxruntime, "
                "faster-whisper, openai-whisper, torch CPU, and insightface-compatible packages. "
                "Shotwright can sync these packages from the configured runtime requirements file into a persistent venv "
                "before execution, so dependency updates do not require rebuilding the Docker image."
            ),
            handler=run_python_code,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project when one exists.",
                    },
                    "work_dir": {
                        "type": "string",
                        "description": (
                            "Optional working directory. Relative paths resolve under the active project workspace "
                            "or this session's _python workspace; absolute paths must stay inside the session uploads, exports, or project workspace."
                        ),
                    },
                    "script_content": {
                        "type": "string",
                        "description": "Complete Python script source to run.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the Python processing step.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout in seconds, from 1 to 900. Defaults to 180.",
                    },
                },
                "required": ["script_content"],
            },
            skip_permission=True,
        ),
        Tool(
            name="create_reference_composition",
            description="Create or update a composition in the active project using a staged reference image, without needing handwritten JSX for the common setup path.",
            handler=create_reference_composition,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    },
                    "reference_asset_path": {
                        "type": "string",
                        "description": "Optional absolute or project-relative image path. When omitted, the most recent session image is staged automatically.",
                    },
                    "asset_name": {
                        "type": "string",
                        "description": "Optional stable file name to use when copying the reference image into the project workspace",
                    },
                    "composition_name": {
                        "type": "string",
                        "description": "Composition name to create or update",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Composition width in pixels",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Composition height in pixels",
                    },
                    "duration_seconds": {
                        "type": "number",
                        "description": "Composition duration in seconds",
                    },
                    "frame_rate": {
                        "type": "number",
                        "description": "Composition frame rate",
                    },
                    "fit_mode": {
                        "type": "string",
                        "description": "Image fit mode: cover or contain",
                    },
                    "reset_existing": {
                        "type": "boolean",
                        "description": "When true, clear existing layers in the target comp before inserting the reference image",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the composition setup step",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="create_lyrics_mv_project",
            description=(
                "Reliable backend path for lyric MV, dense CJK text animation, subtitle-heavy, or text-measurement-sensitive "
                "After Effects scenes. It builds a render-ready vertical composition with paragraph text via addBoxText, "
                "uses sourceRectAtTime to center actual rendered text, writes a measurement report, saves the managed AEP, "
                "and avoids forcing the model to inline a large JSX script. Use this before render_after_effects_project "
                "when lyrics_lrc or assets/data/lyric_mapping.json is available."
            ),
            handler=create_lyrics_mv_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project or creates a new managed workspace.",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project name when creating a new workspace.",
                    },
                    "aep_filename": {
                        "type": "string",
                        "description": "AEP filename when creating a new workspace.",
                    },
                    "lyrics_lrc": {
                        "type": "string",
                        "description": "Optional LRC text using [mm:ss.xx] time tags. If omitted, the tool searches recent user messages or an existing mapping file.",
                    },
                    "mapping_path": {
                        "type": "string",
                        "description": "Optional project-relative lyric mapping JSON path. Defaults to assets/data/lyric_mapping.json.",
                    },
                    "source_start_timecode": {
                        "type": "string",
                        "description": "Source song timecode corresponding to comp time 0. Defaults to 02:07.00.",
                    },
                    "composition_name": {
                        "type": "string",
                        "description": "Composition name to create or replace. Defaults to Main.",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Composition width in pixels. Defaults to 1080.",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Composition height in pixels. Defaults to 1920.",
                    },
                    "duration_seconds": {
                        "type": "number",
                        "description": "Preview duration in seconds, clamped to 40-55. Defaults to 45.",
                    },
                    "frame_rate": {
                        "type": "number",
                        "description": "Composition frame rate. Defaults to 30.",
                    },
                    "audio_path": {
                        "type": "string",
                        "description": "Optional project-relative audio path. If missing, an original placeholder atmosphere WAV is generated.",
                    },
                    "script_path": {
                        "type": "string",
                        "description": "Optional project-relative path for the generated JSX. Defaults to scripts/qinghua_mv_preview.jsx.",
                    },
                    "measurement_report_path": {
                        "type": "string",
                        "description": "Optional project-relative path for text measurement JSON. Defaults to reports/qinghua_mv_measurements.json.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the operation.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution.",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="run_after_effects_jsx",
            description=(
                "Execute a JSX script inside the active After Effects container. When a project_id or active project exists, "
                "the script can use SHOTWRIGHT_PROJECT_ROOT and SHOTWRIGHT_PROJECT_FILE to save updates back into the managed workspace. "
                "For captions, subtitles, title cards, lower thirds, CJK, and any multi-line copy, create paragraph text with "
                "comp.layers.addBoxText([width, height]); never use point text for sentence text and never assign TextDocument.boxText. "
                "After setting Source Text, call sourceRectAtTime(0, false), set Anchor Point to the true rendered center "
                "[rect.left + rect.width / 2, rect.top + rect.height / 2], then set Position to the intended visual center. "
                "For Chinese text, use verified PostScript font names from inspect_workspace.recommended_fonts or app.fonts.allFonts. "
                "Prefer NotoSansSC-Bold/Medium/Regular for readable captions, LXGWWenKai-Medium/Regular for cute sticker text, "
                "and NotoSerifSC-Bold/Medium for title cards. Do not use MS-Gothic or YuGothic for Chinese, and verify "
                "textProp.value.font after setting the TextDocument. For creative subtitle, title, sticker, and lower-third design, "
                "follow inspect_workspace.creative_quality_policy and inspect_workspace.subtitle_style_policy: choose a deliberate "
                "art direction that fits the footage and story, then verify the storyboard shows that intent. Do not treat the policy "
                "as a fixed style recipe or finalize a render that is merely technically valid but visually weak."
            ),
            handler=run_after_effects_jsx,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    },
                    "script_content": {
                        "type": "string",
                        "description": (
                            "Complete JSX script source. Use this only for short scripts. For complex scenes, "
                            "many lyric/text layers, or scripts over roughly 8 KB, first use run_python_code to "
                            "write a .jsx file inside the project workspace, then pass script_path instead."
                        ),
                    },
                    "script_path": {
                        "type": "string",
                        "description": (
                            "Path to a .jsx/.jsxinc/.js file inside this session or active project workspace. "
                            "Relative paths resolve from the active project workspace. Prefer this for complex AE scripts."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the operation",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout for AfterFX.jsx execution",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="render_after_effects_project",
            description=(
                "Render a managed or uploaded After Effects project through nexrender-cli and prepare an HLS preview. "
                "When patch_script is provided, Shotwright first persists that JSX into the managed project so later exports match the preview. "
                "After a successful render, the agent should call generate_storyboard_from_reference_video on the rendered mp4, "
                "review the storyboard for creative intent, pacing, framing, text safety, hierarchy, weak frames, and missing glyphs, "
                "then revise or finalize."
            ),
            handler=render_after_effects_project,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    },
                    "aep_file": {
                        "type": "string",
                        "description": "Relative path to the .aep file inside the uploaded archive",
                    },
                    "composition": {
                        "type": "string",
                        "description": "Composition name to render",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Optional output mp4 file name",
                    },
                    "patch_script": {
                        "type": "string",
                        "description": "Optional absolute path to a JSX patch asset; Shotwright will persist it into the managed project before rendering",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="export_project_archive",
            description="Create a downloadable zip archive for the active uploaded project using the current managed workspace state.",
            handler=export_project_archive,
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project identifier; defaults to the active project",
                    }
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="stop_after_effects_container",
            description="Stop the session's running After Effects container when work is complete.",
            handler=stop_after_effects_container,
            parameters={"type": "object", "properties": {}},
            skip_permission=True,
        ),
    ]
