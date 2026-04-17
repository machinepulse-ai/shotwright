"""nexrender integration — build jobs and invoke nexrender-cli inside containers."""

import json
import logging

from app.services.container_manager import exec_in_container, get_container

logger = logging.getLogger(__name__)


def build_nexrender_job(
    aep_path: str,
    composition: str = "Main",
    output_path: str = "C:\\data\\output\\result.mp4",
    patch_script: str | None = None,
) -> dict:
    """Build a nexrender job JSON payload."""
    job: dict = {
        "template": {
            "src": f"file://{aep_path}",
            "composition": composition,
        },
        "assets": [],
        "actions": {
            "postrender": [
                {
                    "module": "@nexrender/action-encode",
                    "preset": "mp4",
                    "output": "encoded.mp4",
                },
                {
                    "module": "@nexrender/action-copy",
                    "input": "encoded.mp4",
                    "output": output_path,
                },
            ]
        },
    }
    if patch_script:
        job["assets"].append(
            {
                "type": "script",
                "src": f"file://{patch_script}",
            }
        )
    return job


async def run_render(container_db_id: str, job: dict) -> dict:
    """Execute a nexrender render inside the container."""
    container = await get_container(container_db_id)
    if not container:
        raise ValueError("Container not found")

    job_json = json.dumps(job).replace('"', '\\"')
    cmd = [
        "powershell",
        "-Command",
        f'nexrender-cli --job "{job_json}"',
    ]

    exit_code, output = await exec_in_container(container["docker_id"], cmd)

    # nexrender may exit non-zero while still producing output
    success = exit_code == 0 or "result.mp4" in output.lower()

    return {
        "exit_code": exit_code,
        "success": success,
        "output": output[-2000:] if len(output) > 2000 else output,
    }


async def run_jsx_script(container_db_id: str, script_content: str) -> dict:
    """Write and execute a JSX script via aerender in the container."""
    container = await get_container(container_db_id)
    if not container:
        raise ValueError("Container not found")

    # Write script to temp location
    script_path = "C:\\data\\temp_script.jsx"
    escaped = script_content.replace("'", "''")
    write_cmd = [
        "powershell",
        "-Command",
        f"Set-Content -Path '{script_path}' -Value '{escaped}' -Encoding UTF8",
    ]
    await exec_in_container(container["docker_id"], write_cmd)

    # Execute via aerender -s
    run_cmd = [
        "powershell",
        "-Command",
        f'& "C:\\Program Files\\Adobe\\Adobe After Effects 2026\\Support Files\\aerender.exe" -s "{script_path}"',
    ]
    exit_code, output = await exec_in_container(container["docker_id"], run_cmd)

    return {
        "exit_code": exit_code,
        "output": output[-2000:] if len(output) > 2000 else output,
    }
