"""Copilot agent action dispatcher."""

from fastapi import APIRouter, HTTPException

from app.models.agent import AgentCommand, AgentResponse, JsxScript
from app.services import container_manager as cm
from app.services.nexrender import run_jsx_script, run_render, build_nexrender_job
from app.services.project_manager import upload_project

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/command", response_model=AgentResponse)
async def agent_command(body: AgentCommand):
    """Unified agent action endpoint — dispatches to the appropriate service."""
    action = body.action
    payload = body.payload

    try:
        if action == "start_container":
            doc = await cm.create_container(body.session_id, payload.get("image"))
            return AgentResponse(
                success=True, action=action, message="Container started", data={"container": doc}
            )

        elif action == "render_video":
            container_id = payload.get("container_id")
            if not container_id:
                raise ValueError("container_id required")
            job = build_nexrender_job(
                aep_path=payload.get("aep_path", ""),
                composition=payload.get("composition", "Main"),
            )
            result = await run_render(container_id, job)
            return AgentResponse(success=result["success"], action=action, message="Render complete", data=result)

        elif action == "run_jsx":
            container_id = payload.get("container_id")
            script = payload.get("script_content", "")
            if not container_id or not script:
                raise ValueError("container_id and script_content required")
            result = await run_jsx_script(container_id, script)
            return AgentResponse(success=True, action=action, message="JSX executed", data=result)

        else:
            raise HTTPException(400, f"Unsupported action: {action}")

    except ValueError as exc:
        return AgentResponse(success=False, action=action, message=str(exc), data={})
    except Exception as exc:
        return AgentResponse(success=False, action=action, message=f"Internal error: {exc}", data={})


@router.post("/jsx", response_model=AgentResponse)
async def run_jsx(body: JsxScript):
    """Convenience endpoint for running arbitrary JSX in a session's container."""
    from app.database import get_session_collection

    col = get_session_collection()
    session = await col.find_one({"_id": body.session_id})
    if not session or not session.get("container_id"):
        raise HTTPException(400, "Session has no associated container")

    result = await run_jsx_script(session["container_id"], body.script_content)
    return AgentResponse(
        success=True,
        action="run_jsx",
        message=body.description or "JSX script executed",
        data=result,
    )
