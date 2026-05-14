from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import docker


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER_BASE_URL = "npipe:////./pipe/docker_engine"
API_BASE_URL = os.environ.get("SHOTWRIGHT_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEV_CONTAINER_NAME = os.environ.get("SHOTWRIGHT_DEV_CONTAINER", "shotwright-dev")
RUNTIME_IMAGE = os.environ.get("SHOTWRIGHT_RUNTIME_IMAGE", "shotwright:allinone")


def log(message: str) -> None:
    print(message, flush=True)


def api_request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(f"{API_BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API {method} {path} failed: {error.code} {body}") from error


def stream_runtime_build(client: docker.APIClient) -> None:
    log(f"[build] rebuilding {RUNTIME_IMAGE} from {REPO_ROOT}")
    build_args = {
        key: value
        for key in (
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "PIP_INDEX_URL",
            "PIP_TRUSTED_HOST",
            "PIP_DEFAULT_TIMEOUT",
            "NPM_REGISTRY",
            "CHOCO_SOURCE",
        )
        if (value := os.environ.get(key))
    }
    command = [
        "docker",
        "build",
        "--file",
        "Dockerfile",
        "--tag",
        RUNTIME_IMAGE,
        "--target",
        "shotwright",
    ]
    for key, value in build_args.items():
        command.extend(["--build-arg", f"{key}={value}"])
    command.append(".")

    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        text = line.rstrip()
        if text:
            log(f"[build] {text}")

    exit_code = process.wait()
    if exit_code != 0:
        raise RuntimeError(f"docker build failed with exit code {exit_code}")

    inspected = client.inspect_image(RUNTIME_IMAGE)
    log(f"[build] completed image_id={inspected.get('Id')}")


def replace_runtime_workers(client: docker.APIClient) -> list[dict[str, Any]]:
    sessions = {item["_id"]: item for item in api_request("GET", "/api/sessions") or []}
    containers = api_request("GET", "/api/containers") or []
    runtime_workers = [
        item
        for item in containers
        if item.get("status") == "running" and str(item.get("image", "")).lower() == RUNTIME_IMAGE.lower()
    ]

    log(f"[replace] found {len(runtime_workers)} running runtime workers")
    replacements: list[dict[str, Any]] = []
    for item in runtime_workers:
        session = sessions.get(item["session_id"], {})
        session_name = session.get("name", "<unknown session>")
        log(
            f"[replace] session={item['session_id']} name={session_name} old_container_db_id={item['_id']} old_docker_id={item['docker_id'][:12]}"
        )
        new_doc = api_request(
            "POST",
            "/api/containers",
            {"session_id": item["session_id"], "image": RUNTIME_IMAGE},
        )
        docker_info = client.inspect_container(new_doc["docker_id"])
        log(
            f"[replace] started new_container_db_id={new_doc['_id']} new_docker_id={new_doc['docker_id'][:12]} state={docker_info['State']['Status']}"
        )
        api_request("DELETE", f"/api/containers/{item['_id']}")
        log(f"[replace] removed old container_db_id={item['_id']}")
        replacements.append({"old": item, "new": new_doc})

    return replacements


def stream_dev_smoke(client: docker.APIClient) -> int:
    log(f"[smoke] exec into {DEV_CONTAINER_NAME} with python -u runtime_smoke.py")
    exec_id = client.exec_create(
        container=DEV_CONTAINER_NAME,
        cmd=["python", "-u", "C:/workspace/src/scripts/runtime_smoke.py"],
        workdir="C:/workspace",
        stdout=True,
        stderr=True,
    )["Id"]
    for chunk in client.exec_start(exec_id, stream=True, demux=False):
        text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        if not text:
            continue
        for line in text.rstrip().splitlines():
            log(f"[smoke] {line}")
    result = client.exec_inspect(exec_id)
    exit_code = int(result.get("ExitCode") or 0)
    log(f"[smoke] exec finished exit_code={exit_code}")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild shotwright runtime, replace worker containers, and run AE smoke.")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-replace", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()

    client = docker.APIClient(base_url=DOCKER_BASE_URL, version="auto")
    log(f"[health] checking backend at {API_BASE_URL}")
    health = api_request("GET", "/api/health")
    log(f"[health] {json.dumps(health, ensure_ascii=False)}")

    if args.skip_build:
        log("[build] skipped by flag")
    else:
        stream_runtime_build(client)

    if args.skip_replace:
        log("[replace] skipped by flag")
    else:
        replacements = replace_runtime_workers(client)
        log(f"[replace] replaced {len(replacements)} runtime workers")

    if args.skip_smoke:
        log("[smoke] skipped by flag")
    else:
        smoke_exit_code = stream_dev_smoke(client)
        if smoke_exit_code != 0:
            raise SystemExit(smoke_exit_code)

    log("[done] runtime rebuild, worker replacement, and smoke all succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
