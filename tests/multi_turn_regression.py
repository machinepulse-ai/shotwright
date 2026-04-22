from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import PureWindowsPath
from uuid import uuid4


BASE_URL = "http://127.0.0.1:8000/api"
HEADERS = {"Content-Type": "application/json"}


def request(method: str, path: str, payload: dict | None = None) -> dict | list:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=1800) as response:
        return json.load(response)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def docker_read_json(path_text: str) -> dict:
    command = [
        "docker",
        "exec",
        "shotwright-dev",
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$path = '{0}'; "
            "if (-not (Test-Path $path)) {{ Write-Error \"missing:$path\"; exit 2 }}; "
            "Get-Content $path -Raw"
        ).format(path_text.replace("'", "''")),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"docker read failed for {path_text}")
    return json.loads(result.stdout)


def docker_list_render_metadata(session_id: str) -> list[dict]:
    command = [
        "docker",
        "exec",
        "shotwright-dev",
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$items = Get-ChildItem 'C:/data/exports/{0}' -Filter '*.meta.json' -ErrorAction SilentlyContinue | "
            "Select-Object Name,FullName,Length; $items | ConvertTo-Json -Depth 3"
        ).format(session_id),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "docker list failed")
    text = result.stdout.strip()
    if not text:
        return []
    parsed = json.loads(text)
    return parsed if isinstance(parsed, list) else [parsed]


def main() -> int:
    session = request("POST", "/sessions", {"name": f"multi-turn-structured-regression-{uuid4().hex[:8]}"})
    session_id = session["_id"]
    print(f"SESSION {session_id}", flush=True)

    turns = [
        {
            "label": "turn1",
            "expected_output": "round1.mp4",
            "prompt": (
                "Create a 2-second After Effects animation and render it immediately. "
                "Requirements: 1280x720, main composition named Main, navy background, "
                "one white circle moving from left to right. Use Shotwright tools to create the project "
                "and render the output as round1.mp4."
            ),
        },
        {
            "label": "turn2",
            "expected_output": "round2.mp4",
            "prompt": (
                "Continue editing the same project and the same Main composition. Do not create a new project. "
                "Change the background to black and the circle to red, then render round2.mp4."
            ),
        },
        {
            "label": "turn3",
            "expected_output": "round3.mp4",
            "prompt": (
                "Continue editing the same project and the same Main composition. Do not create a new project. "
                "Add a small white title TEST in the top-right corner, then render round3.mp4."
            ),
        },
    ]

    stable_project_id: str | None = None
    for index, turn in enumerate(turns, start=1):
        print(f"START {turn['label']}", flush=True)
        result = request(
            "POST",
            f"/agent/sessions/{session_id}/messages",
            {"content": turn["prompt"], "attachments": []},
        )
        assistant_message = result["assistant_message"]
        session_status = result["session_status"]
        assistant_text = assistant_message.get("content") or ""
        print(f"END {turn['label']} status={session_status}", flush=True)
        print(f"ASSISTANT {turn['label']} {assistant_text[:240]!r}", flush=True)

        require(session_status != "error", f"{turn['label']} returned error status")
        require("timed out" not in assistant_text.lower(), f"{turn['label']} timed out")

        context = request("GET", f"/agent/sessions/{session_id}/context")
        active_project_id = context["session"].get("active_project_id")
        require(active_project_id is not None, f"{turn['label']} missing active project")

        if stable_project_id is None:
            stable_project_id = active_project_id
        require(active_project_id == stable_project_id, f"{turn['label']} changed project id")

        projects = context.get("projects") or []
        active_project = next((item for item in projects if item.get("_id") == stable_project_id), None)
        require(active_project is not None, f"{turn['label']} missing active project record")

        compositions = active_project.get("compositions") or []
        require(len(compositions) >= 1, f"{turn['label']} missing compositions in context")
        require(any(item.get("name") == "Main" for item in compositions), f"{turn['label']} missing Main composition")

        render_outputs = context.get("render_outputs") or []
        require(len(render_outputs) >= index, f"{turn['label']} expected at least {index} render outputs")
        filenames = [item.get("filename") for item in render_outputs]
        require(turn["expected_output"] in filenames, f"{turn['label']} missing render output {turn['expected_output']}")

        print(
            f"CHECK {turn['label']} project={stable_project_id} "
            f"compositions={[item.get('name') for item in compositions]} renders={filenames}",
            flush=True,
        )

    require(stable_project_id is not None, "missing stable project id")
    final_context = request("GET", f"/agent/sessions/{session_id}/context")
    project = next(item for item in final_context["projects"] if item.get("_id") == stable_project_id)
    workspace_dir = project["workspace_dir"]
    sidecar_path = str(PureWindowsPath(workspace_dir) / ".shotwright-project.json")
    sidecar = docker_read_json(sidecar_path)
    metadata_files = docker_list_render_metadata(session_id)
    render_outputs = final_context.get("render_outputs") or []

    require(len(render_outputs) >= 3, "final render_outputs count < 3")
    require(len(metadata_files) >= 3, "final render metadata count < 3")
    require(any(item.get("name") == "Main" for item in sidecar.get("compositions") or []), "sidecar missing Main")

    summary = {
        "session_id": session_id,
        "project_id": stable_project_id,
        "project_workspace_dir": workspace_dir,
        "sidecar_path": sidecar_path,
        "context_compositions": project.get("compositions") or [],
        "sidecar_compositions": sidecar.get("compositions") or [],
        "render_outputs": [
            {
                "filename": item.get("filename"),
                "composition": item.get("composition"),
                "shared_relative_path": item.get("shared_relative_path"),
            }
            for item in render_outputs
        ],
        "render_metadata_files": [item.get("Name") or item.get("name") for item in metadata_files],
    }
    print("FINAL_SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"REGRESSION_FAILED {exc}", flush=True)
        raise