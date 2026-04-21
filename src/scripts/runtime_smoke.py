from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


BACKEND_ROOT = Path("C:/workspace/src/backend")
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def log(message: str) -> None:
    print(message, flush=True)


def build_smoke_jsx() -> str:
    return """
app.beginUndoGroup("Shotwright smoke");
function findComp(name) {
    for (var index = 1; index <= app.project.items.length; index += 1) {
        var item = app.project.items[index];
        if (item instanceof CompItem && item.name === name) {
            return item;
        }
    }
    return null;
}
function removeSmokeLayers(comp) {
    for (var index = comp.layers.length; index >= 1; index -= 1) {
        var layer = comp.layers[index];
        if (layer && layer.name && layer.name.indexOf("Shotwright Smoke") === 0) {
            layer.remove();
        }
    }
}
var comp = findComp("Main") || findComp("main");
if (!comp) {
    comp = app.project.items.addComp("Main", 1920, 1080, 1, 4, 25);
}
comp.name = "Main";
comp.duration = 4;
comp.frameRate = 25;
removeSmokeLayers(comp);
var background = comp.layers.addSolid([0.08, 0.22, 0.44], "Shotwright Smoke Background", 1920, 1080, 1, 4);
background.moveToEnd();
var textLayer = comp.layers.addText("Shotwright nexrender smoke");
textLayer.name = "Shotwright Smoke Title";
var textDocument = textLayer.property("Source Text").value;
textDocument.fontSize = 84;
textDocument.fillColor = [1, 1, 1];
textDocument.justification = ParagraphJustification.CENTER_JUSTIFY;
textLayer.property("Source Text").setValue(textDocument);
textLayer.property("Position").setValue([960, 540]);
app.endUndoGroup();
""".strip()


async def main() -> int:
    from app.config import settings
    from app.database import close_db, connect_db, get_session_collection
    from app.services import container_manager as cm
    from app.services import nexrender as nr
    from app.services import project_manager as pm

    session_id = f"smoke-{uuid4()}"
    now = datetime.now(timezone.utc)
    session_doc = {
        "_id": session_id,
        "name": f"AE warm host smoke {session_id[:8]}",
        "status": "idle",
        "copilot_model": settings.copilot_model,
        "copilot_reasoning_effort": settings.copilot_reasoning_effort,
        "copilot_session_id": None,
        "container_id": None,
        "active_project_id": None,
        "latest_render_path": None,
        "latest_stream_url": None,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }

    await connect_db()
    container_doc = None
    project_doc = None
    try:
        log(f"[smoke] creating session {session_id}")
        await get_session_collection().insert_one(session_doc)

        log(f"[smoke] starting worker from image {settings.shotwright_image}")
        container_doc = await cm.create_container(session_id, settings.shotwright_image)
        log(f"[smoke] container_db_id={container_doc['_id']} docker_id={container_doc['docker_id'][:12]}")

        project_doc = await pm.create_project_workspace(
            session_id,
            project_name="ae-warm-host-smoke",
            aep_filename="ae-warm-host-smoke.aep",
            set_active=True,
        )
        log(
            f"[smoke] created managed project {project_doc['_id']} at {project_doc['workspace_dir']}"
        )

        jsx_result = await nr.run_jsx_script(
            container_doc["_id"],
            build_smoke_jsx(),
            project=project_doc,
            timeout_seconds=240,
        )
        log("[smoke] JSX result:")
        print(json.dumps(jsx_result, ensure_ascii=False, indent=2), flush=True)
        if not jsx_result.get("success"):
            raise RuntimeError("run_jsx_script smoke failed")

        refreshed_project = await pm.refresh_project_files(session_id, project_doc["_id"])
        if not refreshed_project or not refreshed_project.get("entry_aep_file"):
            raise RuntimeError("managed smoke project did not produce an .aep file")
        log(
            f"[smoke] refreshed project entry_aep_file={refreshed_project['entry_aep_file']}"
        )

        render_result = await nr.render_project(
            session_id=session_id,
            project_id=project_doc["_id"],
            container_db_id=container_doc["_id"],
            composition="Main",
            output_name="ae-warm-host-smoke.mp4",
            timeout_seconds=900,
        )
        log("[smoke] render result:")
        print(json.dumps(render_result, ensure_ascii=False, indent=2), flush=True)
        if not render_result.get("success"):
            raise RuntimeError("render_after_effects_project smoke failed")

        archive_path = await pm.export_project(session_id, project_doc["_id"])
        payload = {
            "session_id": session_id,
            "container_db_id": container_doc["_id"],
            "container_docker_id": container_doc["docker_id"],
            "project_id": project_doc["_id"],
            "workspace_dir": project_doc["workspace_dir"],
            "entry_aep_file": refreshed_project.get("entry_aep_file"),
            "render_output_path": render_result.get("output_path"),
            "archive_path": str(archive_path) if archive_path else None,
        }
        log("[smoke] final payload:")
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as exc:
        log(f"[smoke] ERROR: {exc}")
        raise
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))