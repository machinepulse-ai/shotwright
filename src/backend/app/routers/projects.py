"""Project upload / download / render router."""

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services import project_manager as pm

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/{session_id}")
async def list_projects(session_id: str):
    return await pm.list_projects(session_id)


@router.post("/{session_id}/upload")
async def upload_project(session_id: str, file: UploadFile):
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip files accepted")
    data = await file.read()
    if len(data) > 500 * 1024 * 1024:  # 500 MB limit
        raise HTTPException(413, "File too large")
    result = await pm.upload_project(session_id, data, file.filename)
    return result


@router.get("/{session_id}/{project_id}/archive")
async def export_project_archive(session_id: str, project_id: str):
    path = await pm.export_project(session_id, project_id)
    if not path or not path.exists():
        raise HTTPException(404, "Export not found")
    return FileResponse(path, media_type="application/zip", filename=path.name)
