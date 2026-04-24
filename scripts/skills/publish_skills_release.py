from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

import requests
import urllib3

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from shotwright_config import build_resolved_config, get_default_config_path, load_config


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_TIMEOUT = (30, 180)
UPLOAD_TIMEOUT = None
PROGRESS_UPDATE_INTERVAL_SECONDS = 0.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update the skills bundle GitHub prerelease and upload assets.")
    parser.add_argument("--config", type=Path, default=get_default_config_path())
    parser.add_argument("--artifact-dir", type=Path, default=Path("dist"))
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--proxy", default=None)
    return parser.parse_args()


def resolve_repo(explicit_repo: str | None) -> str:
    if explicit_repo:
        return explicit_repo
    remote_url = subprocess.check_output(
        ["git", "remote", "get-url", "origin"],
        text=True,
        encoding="utf-8",
    ).strip()
    if remote_url.endswith(".git"):
        remote_url = remote_url[:-4]
    marker = "github.com"
    if marker not in remote_url:
        raise ValueError(f"Unsupported origin remote: {remote_url}")
    suffix = remote_url.split(marker, 1)[1].lstrip(":/")
    owner, repo = suffix.split("/", 1)
    return f"{owner}/{repo}"


def format_rate(bytes_per_second: float) -> str:
    if bytes_per_second >= 1024 * 1024:
        return f"{bytes_per_second / (1024 * 1024):.2f} MiB/s"
    if bytes_per_second >= 1024:
        return f"{bytes_per_second / 1024:.2f} KiB/s"
    return f"{bytes_per_second:.0f} B/s"


class UploadProgressReader:
    def __init__(self, asset_path: Path) -> None:
        self.asset_path = asset_path
        self.file = asset_path.open("rb")
        self.size = asset_path.stat().st_size
        self.bytes_read = 0
        self.started_at = time.monotonic()
        self.last_report_at = 0.0

    def __len__(self) -> int:
        return self.size

    def read(self, amount: int = -1) -> bytes:
        chunk = self.file.read(amount)
        if chunk:
            self.bytes_read += len(chunk)
            self.report_progress(force=False)
        else:
            self.report_progress(force=True)
        return chunk

    def close(self) -> None:
        self.file.close()

    def report_progress(self, *, force: bool) -> None:
        now = time.monotonic()
        if not force and (now - self.last_report_at) < PROGRESS_UPDATE_INTERVAL_SECONDS and self.bytes_read < self.size:
            return

        elapsed = max(now - self.started_at, 0.001)
        percent = (self.bytes_read / self.size * 100.0) if self.size else 100.0
        rate = self.bytes_read / elapsed
        print(
            f"  {self.asset_path.name}: {self.bytes_read}/{self.size} bytes ({percent:.1f}%) at {format_rate(rate)}",
            flush=True,
        )
        self.last_report_at = now

    def __enter__(self) -> UploadProgressReader:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class GitHubReleaseClient:
    def __init__(self, token: str, repo: str, proxy: str | None) -> None:
        self.repo = repo
        self.proxy = proxy
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "shotwright-release-script",
            "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})

    def request_json(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> object | None:
        response = self.session.request(
            method,
            url,
            json=data,
            timeout=API_TIMEOUT,
            verify=False,
        )
        if allow_not_found and response.status_code == 404:
            return None
        if response.status_code >= 400:
            detail = response.text.strip() or response.reason
            raise RuntimeError(f"GitHub API {method} {url} failed with {response.status_code}: {detail}")

        payload = response.content
        if not payload:
            return None
        return response.json()

    def get_release_by_tag(self, tag: str) -> dict[str, object] | None:
        return self.request_json(
            "GET",
            f"https://api.github.com/repos/{self.repo}/releases/tags/{tag}",
            allow_not_found=True,
        )  # type: ignore[return-value]

    def create_release(self, *, tag: str, name: str, body: str) -> dict[str, object]:
        return self.request_json(
            "POST",
            f"https://api.github.com/repos/{self.repo}/releases",
            data={
                "tag_name": tag,
                "name": name,
                "body": body,
                "prerelease": True,
                "make_latest": "false",
            },
        )  # type: ignore[return-value]

    def update_release(self, release_id: int, *, name: str, body: str) -> dict[str, object]:
        return self.request_json(
            "PATCH",
            f"https://api.github.com/repos/{self.repo}/releases/{release_id}",
            data={
                "name": name,
                "body": body,
                "prerelease": True,
                "make_latest": "false",
            },
        )  # type: ignore[return-value]

    def delete_asset(self, asset_id: int) -> None:
        self.request_json("DELETE", f"https://api.github.com/repos/{self.repo}/releases/assets/{asset_id}")

    def upload_asset(self, upload_url: str, asset_path: Path, *, content_type: str) -> dict[str, object]:
        clean_upload_url = upload_url.replace("{?name,label}", "")
        response_path = asset_path.parent / f".{asset_path.name}.upload-response.json"
        size_mib = asset_path.stat().st_size / (1024 * 1024)
        print(f"Uploading {asset_path.name} ({size_mib:.2f} MiB)...", flush=True)

        with UploadProgressReader(asset_path) as reader:
            response = self.session.post(
                clean_upload_url,
                params={"name": asset_path.name},
                data=reader,
                headers={
                    "Connection": "close",
                    "Content-Type": content_type,
                    "Content-Length": str(reader.size),
                },
                timeout=UPLOAD_TIMEOUT,
                verify=False,
            )

        response_path.write_text(response.text, encoding="utf-8")
        if response.status_code >= 400:
            raise RuntimeError(
                f"GitHub asset upload failed for {asset_path.name} with HTTP {response.status_code}: {response.text}"
            )

        uploaded = response.json()
        print(f"Uploaded {asset_path.name} -> {uploaded['browser_download_url']}", flush=True)
        response_path.unlink(missing_ok=True)
        return uploaded


def ensure_release(
    client: GitHubReleaseClient,
    *,
    tag: str,
    name: str,
    body: str,
) -> dict[str, object]:
    release = client.get_release_by_tag(tag)
    if release is None:
        return client.create_release(tag=tag, name=name, body=body)
    return client.update_release(int(release["id"]), name=name, body=body)


def main() -> int:
    args = parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Environment variable {args.token_env} is not set.")

    if args.proxy:
        os.environ["HTTPS_PROXY"] = args.proxy
        os.environ["https_proxy"] = args.proxy
        os.environ["HTTP_PROXY"] = args.proxy
        os.environ["http_proxy"] = args.proxy

    config = load_config(args.config.resolve())
    resolved = build_resolved_config(config)
    repo_root = Path(__file__).resolve().parents[2]
    artifact_dir = (repo_root / args.artifact_dir).resolve() if not args.artifact_dir.is_absolute() else args.artifact_dir.resolve()
    artifact_path = artifact_dir / resolved.skills.artifact_file_name
    checksum_path = artifact_dir / f"{resolved.skills.artifact_file_name}.sha256"
    if not artifact_path.is_file():
        raise SystemExit(f"Artifact not found: {artifact_path}")
    if not checksum_path.is_file():
        raise SystemExit(f"Checksum not found: {checksum_path}")

    result_path = artifact_dir / "release_publish_result.json"
    error_path = artifact_dir / "release_publish_error.txt"

    repo = resolve_repo(args.repo)
    client = GitHubReleaseClient(token=token, repo=repo, proxy=args.proxy)
    release_name = f"Shotwright skills bundle {resolved.skills.artifact_version}"
    release_body = (
        "Automated prerelease for the versioned Shotwright Copilot skills bundle. "
        f"Asset {resolved.skills.artifact_file_name} is derived from the repo-local .github/skills tree."
    )
    try:
        print(f"Ensuring release {resolved.skills.release_tag} in {repo}...", flush=True)
        release = ensure_release(
            client,
            tag=resolved.skills.release_tag,
            name=release_name,
            body=release_body,
        )

        existing_assets = {asset["name"]: asset for asset in release.get("assets", [])}
        for asset_name in (artifact_path.name, checksum_path.name):
            if asset_name in existing_assets:
                print(f"Deleting existing asset {asset_name}...", flush=True)
                client.delete_asset(int(existing_assets[asset_name]["id"]))

        zip_asset = client.upload_asset(str(release["upload_url"]), artifact_path, content_type="application/zip")
        checksum_asset = client.upload_asset(
            str(release["upload_url"]),
            checksum_path,
            content_type="text/plain; charset=utf-8",
        )
        final_release = client.get_release_by_tag(resolved.skills.release_tag)
        if final_release is None:
            raise RuntimeError(f"Release {resolved.skills.release_tag} disappeared after upload.")

        result = {
            "tag": final_release["tag_name"],
            "id": final_release["id"],
            "html_url": final_release["html_url"],
            "assets": [
                {
                    "name": asset["name"],
                    "browser_download_url": asset["browser_download_url"],
                }
                for asset in final_release.get("assets", [])
            ],
            "uploaded": [zip_asset["name"], checksum_asset["name"]],
        }
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        error_path.unlink(missing_ok=True)
        print(json.dumps(result, indent=2), flush=True)
        return 0
    except Exception:
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(main())