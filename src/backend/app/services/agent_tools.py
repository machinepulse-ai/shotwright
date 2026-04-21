"""Custom Copilot tools exposing Shotwright container and project controls."""

from __future__ import annotations

import json
from pathlib import Path

from copilot.tools import Tool, ToolInvocation, ToolResult

from app.database import get_session_collection
from app.services import container_manager as cm
from app.services import nexrender as nr
from app.services import project_manager as pm
from app.services.session_streams import publish_context_refresh, publish_session_updated
from app.services.video_streaming import generate_hls


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


def build_shotwright_tools(app_session_id: str) -> list[Tool]:
    """Build session-scoped tools for the Copilot runtime."""

    def _coerce_timeout_seconds(raw_value: object, default: int = 300) -> int:
        if raw_value is None:
            return default
        try:
            return max(30, int(raw_value))
        except (TypeError, ValueError):
            return default

    async def inspect_workspace(invocation: ToolInvocation) -> ToolResult:
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")

        container = None
        if session_doc.get("container_id"):
            container = await cm.get_container(session_doc["container_id"])

        projects = await pm.list_projects(app_session_id)
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
                }
                for project in projects
            ],
            "latest_render_path": session_doc.get("latest_render_path"),
            "latest_stream_url": session_doc.get("latest_stream_url"),
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

        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": app_session_id})
        if not session_doc:
            return _tool_failure("Shotwright session not found.")
        if not session_doc.get("container_id"):
            return _tool_failure("No running After Effects container is attached to this session.")

        project = await pm.create_project_workspace(
            app_session_id,
            project_name=args.get("project_name"),
            aep_filename=args.get("aep_filename"),
            set_active=False,
        )
        result = await nr.run_jsx_script(
            session_doc["container_id"],
            script_content,
            project=project,
            timeout_seconds=_coerce_timeout_seconds(args.get("timeout_seconds")),
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
                session_log=args.get("description") or "Failed to create After Effects project",
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
            args.get("description") or f"Created After Effects project {active_project['filename']}",
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
            description="Read the current Shotwright session state, container status, uploaded projects, and latest render info.",
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
