from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import close_db, connect_db, get_container_collection, get_event_collection, get_message_collection, get_project_collection, get_session_collection
from app.services import container_manager as cm
from app.services import nexrender as nr
from app.services import project_manager as pm
from app.services import reference_media as rm
from app.services.agent_tools import _build_empty_project_jsx, _build_reference_composition_jsx


def log(message: str) -> None:
    print(message, flush=True)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_smoke_session(name: str) -> dict:
    session_id = f"storyboard-smoke-{uuid4().hex[:8]}"
    session_doc = {
        "_id": session_id,
        "name": name,
        "status": "idle",
        "copilot_model": "gpt-5.4",
        "copilot_reasoning_effort": "high",
        "copilot_session_id": None,
        "container_id": None,
        "active_project_id": None,
        "latest_render_path": None,
        "latest_stream_url": None,
        "last_error": None,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    await get_session_collection().insert_one(session_doc)
    return session_doc


async def cleanup_session(session_id: str) -> None:
    await get_container_collection().delete_many({"session_id": session_id})
    await get_project_collection().delete_many({"session_id": session_id})
    await get_message_collection().delete_many({"session_id": session_id})
    await get_event_collection().delete_many({"session_id": session_id})
    await get_session_collection().delete_one({"_id": session_id})


async def run_smoke(args: argparse.Namespace) -> dict:
    session = await create_smoke_session("Storyboard Reference Smoke")
    session_id = session["_id"]
    container_doc: dict | None = None

    try:
        reference_video_path = Path(args.reference_video).resolve()
        if not reference_video_path.exists():
            raise FileNotFoundError(f"Reference video not found: {reference_video_path}")

        log(f"[reference] upload {reference_video_path}")
        reference_video = rm.upload_reference_video(session_id, reference_video_path.read_bytes(), reference_video_path.name)

        log("[storyboard] generate storyboard contact sheet")
        storyboard = rm.generate_storyboard(
            session_id,
            reference_video_path=reference_video["shared_relative_path"],
            interval_seconds=args.interval_seconds,
            clip_duration_seconds=args.clip_duration_seconds,
            columns=args.columns,
            width=args.width,
        )

        log("[container] ensure After Effects container")
        container_doc = await cm.create_container(session_id, image=args.image or None)

        log("[project] create managed project workspace")
        project = await pm.create_project_workspace(
            session_id,
            project_name=args.project_name,
            aep_filename=args.aep_filename,
            set_active=True,
        )

        log("[project] save empty AEP")
        empty_project_result = await nr.run_jsx_script(
            container_doc["_id"],
            _build_empty_project_jsx(),
            project=project,
            timeout_seconds=args.jsx_timeout_seconds,
        )
        project = await pm.refresh_project_files(session_id, project["_id"]) or project

        log("[project] create Main composition from storyboard")
        composition_result = await nr.run_jsx_script(
            container_doc["_id"],
            _build_reference_composition_jsx(
                reference_asset_path=storyboard["storyboard_image_path"],
                composition_name=args.composition_name,
                width=args.comp_width,
                height=args.comp_height,
                duration_seconds=args.comp_duration_seconds,
                frame_rate=args.frame_rate,
                fit_mode=args.fit_mode,
                reset_existing=True,
            ),
            project=project,
            timeout_seconds=args.jsx_timeout_seconds,
        )
        project = await pm.refresh_project_files(session_id, project["_id"]) or project

        log("[render] export mp4 through nexrender")
        render_result = await nr.render_project(
            session_id,
            project["_id"],
            container_doc["_id"],
            composition=args.composition_name,
            output_name=args.output_name,
            timeout_seconds=args.render_timeout_seconds,
        )

        entry_aep_file = project.get("entry_aep_file") or (project.get("aep_files") or [None])[0]
        entry_aep_path = str(Path(project["workspace_dir"]) / entry_aep_file) if entry_aep_file else None
        entry_aep_exists = bool(entry_aep_path and Path(entry_aep_path).exists())

        summary = {
            "session_id": session_id,
            "reference_video_path": reference_video["reference_video_path"],
            "storyboard_image_path": storyboard["storyboard_image_path"],
            "project_id": project["_id"],
            "entry_aep_path": entry_aep_path,
            "container_id": container_doc["_id"],
            "empty_project_success": bool(empty_project_result.get("success", empty_project_result.get("exit_code") == 0)) or entry_aep_exists,
            "composition_success": composition_result.get("success", composition_result.get("exit_code") == 0),
            "render_success": render_result.get("success"),
            "render_output_path": render_result.get("output_path"),
            "render_output_exists": Path(str(render_result.get("output_path") or "")).exists(),
        }
        return summary
    finally:
        if container_doc and not args.keep_container:
            try:
                await cm.stop_container(container_doc["_id"])
            except Exception as exc:
                log(f"[cleanup] failed to stop container {container_doc['_id']}: {exc}")
        if args.cleanup_session:
            await cleanup_session(session_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the storyboard -> AEP -> MP4 flow with a reference video.")
    parser.add_argument(
        "--reference-video",
        default=str(REPO_ROOT / "validation-data" / "templates" / "lyrics.mp4"),
        help="Path to the source reference video",
    )
    parser.add_argument("--image", default="", help="Optional container image override")
    parser.add_argument("--project-name", default="lyrics-reference-smoke")
    parser.add_argument("--aep-filename", default="lyrics-reference-smoke.aep")
    parser.add_argument("--composition-name", default="Main")
    parser.add_argument("--output-name", default=f"storyboard-smoke-{uuid4().hex[:8]}.mp4")
    parser.add_argument("--interval-seconds", type=float, default=0.75)
    parser.add_argument("--clip-duration-seconds", type=float, default=6.0)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--width", type=int, default=220)
    parser.add_argument("--comp-width", type=int, default=1920)
    parser.add_argument("--comp-height", type=int, default=1080)
    parser.add_argument("--comp-duration-seconds", type=float, default=6.0)
    parser.add_argument("--frame-rate", type=float, default=30.0)
    parser.add_argument("--fit-mode", choices=["cover", "contain"], default="contain")
    parser.add_argument("--jsx-timeout-seconds", type=int, default=300)
    parser.add_argument("--render-timeout-seconds", type=int, default=900)
    parser.add_argument("--keep-container", action="store_true")
    parser.add_argument("--cleanup-session", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    await connect_db()
    try:
        summary = await run_smoke(args)
        log(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["render_success"] and summary["render_output_exists"] else 1
    finally:
        await close_db()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())