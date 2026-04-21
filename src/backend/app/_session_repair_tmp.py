import asyncio
import json
from pathlib import Path

from app.database import close_db, connect_db, get_session_collection
from app.services import nexrender as nr
from app.services import project_manager as pm
from app.services.video_streaming import generate_hls

SESSION_ID = "330013d0-e70d-4306-95c4-a225b69b11dd"
PROJECT_ID = "44d92bde-fed4-4adb-9dbd-64cfdeda5e2a"
SCRIPT_PATH = Path(r"C:\data\exports\330013d0-e70d-4306-95c4-a225b69b11dd\_nexrender_jsx\14a96f29\user-script.jsx")


async def main() -> None:
    await connect_db()
    try:
        session_col = get_session_collection()
        session_doc = await session_col.find_one({"_id": SESSION_ID})
        project = await pm.get_project(SESSION_ID, PROJECT_ID)
        if not session_doc:
            raise RuntimeError("Session not found")
        if not project:
            raise RuntimeError("Project not found")

        script_content = SCRIPT_PATH.read_text(encoding="utf-8")
        jsx_result = await nr.run_jsx_script(
            session_doc["container_id"],
            script_content,
            project=project,
            timeout_seconds=300,
        )
        refreshed = await pm.refresh_project_files(SESSION_ID, PROJECT_ID)

        render_result = None
        stream_result = None
        if refreshed and refreshed.get("aep_files"):
            render_result = await nr.render_project(
                session_id=SESSION_ID,
                project_id=PROJECT_ID,
                container_db_id=session_doc["container_id"],
                composition="Main",
                output_name="simple_text_3d_animation.mp4",
            )
            if render_result.get("success"):
                stream_result = await generate_hls(render_result["output_path"], render_result["stream_id"])
                latest_stream_url = stream_result.get("playlist_url") if stream_result.get("success") else None
                await session_col.update_one(
                    {"_id": SESSION_ID},
                    {
                        "$set": {
                            "latest_render_path": render_result["output_path"],
                            "latest_stream_id": render_result["stream_id"],
                            "latest_stream_url": latest_stream_url,
                            "status": "idle",
                            "last_error": None,
                        }
                    },
                )

        session_after = await session_col.find_one({"_id": SESSION_ID})
        print(
            json.dumps(
                {
                    "jsx_result": jsx_result,
                    "refreshed_project": refreshed,
                    "render_result": render_result,
                    "stream_result": stream_result,
                    "session_after": session_after,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
