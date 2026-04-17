"""Project upload / download / render router."""

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services import project_manager as pm
from app.services.nexrender import build_nexrender_job, run_render
from app.services.video_streaming import generate_hls

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("/{session_id}/upload")
async def upload_project(session_id: str, container_id: str, file: UploadFile):
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip files accepted")
    data = await file.read()
    if len(data) > 500 * 1024 * 1024:  # 500 MB limit
        raise HTTPException(413, "File too large")
    result = await pm.upload_project(session_id, container_id, data, file.filename)
    return result


@router.post("/{session_id}/export")
async def export_project(session_id: str, container_id: str, project_id: str):
    path = await pm.export_project(session_id, container_id, project_id)
    if not path or not path.exists():
        raise HTTPException(404, "Export not found")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.post("/{session_id}/render")
async def render_project(
    session_id: str,
    container_id: str,
    aep_path: str,
    composition: str = "Main",
):
    job = build_nexrender_job(aep_path, composition)
    result = await run_render(container_id, job)
    return result


@router.post("/{session_id}/stream")
async def create_stream(session_id: str, mp4_path: str):
    result = await generate_hls(mp4_path, session_id)
    if not result["success"]:
        raise HTTPException(500, result.get("error", "HLS conversion failed"))
    return result
