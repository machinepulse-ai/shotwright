"""Custom Copilot tools exposing Shotwright container and project controls."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from copilot.tools import Tool, ToolInvocation, ToolResult

from app.database import get_message_collection, get_session_collection
from app.services import container_manager as cm
from app.services import nexrender as nr
from app.services import project_manager as pm
from app.services import reference_media as rm
from app.services.session_streams import publish_context_refresh, publish_session_updated
from app.services.video_streaming import generate_hls

_REFERENCE_ASSET_DIRECTORY = Path("assets") / "references"


def _tool_success(payload: dict, session_log: str) -> ToolResult:
    return ToolResult(
        text_result_for_llm=json.dumps(payload, ensure_ascii=False),
        result_type="success",
        session_log=session_log,
    )


def _tool_failure(message: str, *, error: str | None = None) -> ToolResult:
    return ToolResult(
        text_result_for_llm=message,
        result_type="failure",
        error=error or message,
    )


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
            "var projectFile = $.getenv(\"SHOTWRIGHT_PROJECT_FILE\");",
            "if (!projectFile) { throw new Error(\"SHOTWRIGHT_PROJECT_FILE missing\"); }",
            "app.newProject();",
            "app.project.save(new File(projectFile));",
            "app.quit();",
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

        project = await pm.create_project_workspace(
            app_session_id,
            project_name=arguments.get("project_name"),
            aep_filename=arguments.get("aep_filename"),
            set_active=False,
        )
        result = await nr.run_jsx_script(
            session_doc["container_id"],
            script_content,
            project=project,
            timeout_seconds=_coerce_timeout_seconds(arguments.get("timeout_seconds")),
        )
        refreshed_project = await pm.refresh_project_files(app_session_id, project["_id"])
        if not refreshed_project or not refreshed_project.get("aep_files"):
            payload = {
                **result,
                "project_id": project["_id"],
                "workspace_dir": project["workspace_dir"],
                "entry_aep_file": project.get("entry_aep_file"),
                "entry_aep_path": str(Path(project["workspace_dir"]) / project["entry_aep_file"]),
            }
            return ToolResult(
                text_result_for_llm=json.dumps(payload, ensure_ascii=False),
                result_type="failure",
                error=(
                    "JSX did not save an .aep file into the managed project workspace. "
                    "Save the project to SHOTWRIGHT_PROJECT_FILE and call app.quit()."
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
                    "workspace_dir": project["workspace_dir"],
                }
                for project in projects
            ],
            "recent_image_attachments": recent_image_attachments,
            "latest_render_path": session_doc.get("latest_render_path"),
            "latest_stream_url": session_doc.get("latest_stream_url"),
            "reference_videos": rm.list_reference_videos(app_session_id, limit=8),
            "storyboards": rm.list_storyboards(app_session_id, limit=8),
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
        return await _create_project_from_script(
            arguments=args,
            script_content=_build_empty_project_jsx(),
            default_description="Created empty After Effects project",
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
            )
        except rm.ReferenceMediaUnavailableError as exc:
            return _tool_failure(str(exc), error=str(exc))
        except (FileNotFoundError, TypeError, ValueError) as exc:
            return _tool_failure(str(exc))

        return _tool_success(
            payload,
            args.get("description") or "Generated storyboard from the reference video",
        )

    async def run_after_effects_jsx(invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments or {}
        script_content = (args.get("script_content") or "").strip()
        if not script_content:
            return _tool_failure("script_content is required.")

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

        result = await nr.run_jsx_script(
            session_doc["container_id"],
            script_content,
            project=project,
            timeout_seconds=_coerce_timeout_seconds(args.get("timeout_seconds")),
        )
        payload = dict(result)
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

        result_type = "success" if result.get("success", result.get("exit_code") == 0) else "failure"
        return ToolResult(
            text_result_for_llm=json.dumps(payload, ensure_ascii=False),
            result_type=result_type,
            error=result.get("output") if result_type == "failure" else None,
            session_log=args.get("description") or "Executed After Effects JSX script",
        )

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

        render = await nr.render_project(
            session_id=app_session_id,
            project_id=project_id,
            container_db_id=session_doc["container_id"],
            aep_relative_path=args.get("aep_file"),
            composition=args.get("composition") or "Main",
            output_name=args.get("output_name"),
            patch_script=args.get("patch_script"),
        )
        if not render["success"]:
            return ToolResult(
                text_result_for_llm=json.dumps(render, ensure_ascii=False),
                result_type="failure",
                error=render.get("output") or "Render failed",
                session_log="After Effects render failed",
            )

        stream_result = await generate_hls(render["output_path"], render["stream_id"])
        latest_stream_url = stream_result.get("playlist_url") if stream_result.get("success") else None
        await session_col.update_one(
            {"_id": app_session_id},
            {
                "$set": {
                    "latest_render_path": render["output_path"],
                    "latest_stream_id": render["stream_id"],
                    "latest_stream_url": latest_stream_url,
                }
            },
        )
        await publish_session_updated(app_session_id)
        await publish_context_refresh(app_session_id, "render.completed", project_id=project_id)

        payload = {
            **render,
            "playlist_url": latest_stream_url,
            "stream_ready": bool(latest_stream_url),
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
                "Create a new managed Shotwright project workspace, run an After Effects JSX script to save an .aep into it, "
                "and make that project active for later render/export steps."
            ),
            handler=create_after_effects_project,
            parameters={
                "type": "object",
                "properties": {
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
                            "Complete JSX script source. Save the project to SHOTWRIGHT_PROJECT_FILE and call app.quit() when finished."
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
            description="Create a blank managed Shotwright .aep and make it active without requiring handwritten JSX boilerplate.",
            handler=create_empty_after_effects_project,
            parameters={
                "type": "object",
                "properties": {
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
            description="Generate a storyboard contact sheet from an uploaded session reference video using ffmpeg sampling parameters, without falling back to shell commands.",
            handler=generate_storyboard_from_reference_video,
            parameters={
                "type": "object",
                "properties": {
                    "reference_video_path": {
                        "type": "string",
                        "description": "Optional shared-relative or absolute path to an uploaded session reference video; defaults to the newest uploaded reference video",
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
                    "description": {
                        "type": "string",
                        "description": "Short human-readable description of the storyboard generation step",
                    },
                },
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
            name="run_after_effects_jsx",
            description=(
                "Execute a JSX script inside the active After Effects container. When a project_id or active project exists, "
                "the script can use SHOTWRIGHT_PROJECT_ROOT and SHOTWRIGHT_PROJECT_FILE to save updates back into the managed workspace."
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
                        "description": "Complete JSX script source",
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
                "required": ["script_content"],
            },
            skip_permission=True,
        ),
        Tool(
            name="render_after_effects_project",
            description="Render a managed or uploaded After Effects project through nexrender-cli and prepare an HLS preview.",
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
                        "description": "Optional absolute container path to a JSX patch asset used by nexrender",
                    },
                },
            },
            skip_permission=True,
        ),
        Tool(
            name="export_project_archive",
            description="Create a downloadable zip archive for the active uploaded project.",
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