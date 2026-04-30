from __future__ import annotations

import math
import hashlib
import json
import os
import subprocess
import shutil
import ssl
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from zipfile import ZipFile

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from shotwright_config import build_resolved_config, get_default_config_path, load_config


BUNDLE_MANIFEST_NAME = "skills-bundle-manifest.json"
LOCAL_METADATA_FILE_NAME = ".bundle-manifest.json"
SKILLS_ARCHIVE_PREFIX = PurePosixPath(".github/skills")
DOWNLOAD_TIMEOUT_SECONDS = int(os.environ.get("SHOTWRIGHT_SKILLS_DOWNLOAD_TIMEOUT_SECONDS", "3600"))
DOWNLOAD_USER_AGENT = "shotwright-skills-bundle"
GITHUB_API_VERSION = "2022-11-28"
URL_PROXY_PREFIX_ENV_NAME = "SHOTWRIGHT_SKILLS_URL_PROXY_PREFIX"
DOWNLOAD_CONCURRENCY = max(1, int(os.environ.get("SHOTWRIGHT_SKILLS_DOWNLOAD_CONCURRENCY", "8")))
MULTIPART_DOWNLOAD_MIN_BYTES = int(
    os.environ.get("SHOTWRIGHT_SKILLS_MULTIPART_MIN_BYTES", str(8 * 1024 * 1024))
)
DOWNLOAD_PROGRESS_UPDATE_SECONDS = float(
    os.environ.get("SHOTWRIGHT_SKILLS_DOWNLOAD_PROGRESS_UPDATE_SECONDS", "0.5")
)
DOWNLOAD_BUFFER_SIZE = int(
    os.environ.get("SHOTWRIGHT_SKILLS_DOWNLOAD_BUFFER_SIZE", str(1024 * 1024 * 8))
)
DOWNLOAD_BACKEND = os.environ.get("SHOTWRIGHT_SKILLS_DOWNLOAD_BACKEND", "").strip().lower()
PLACEHOLDER_SKILL_MARKERS = (
    "local placeholder skill descriptor",
)


class SkillsBundleError(RuntimeError):
    """Raised when the skills bundle cannot be downloaded or unpacked."""


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_url_proxy_prefix(url_proxy_prefix: str | None) -> str | None:
    if not url_proxy_prefix:
        return None
    normalized = url_proxy_prefix.strip()
    if not normalized:
        return None
    return normalized if normalized.endswith("/") else f"{normalized}/"


def _apply_url_proxy_prefix(url: str, url_proxy_prefix: str | None) -> str:
    normalized = _normalize_url_proxy_prefix(url_proxy_prefix)
    if not normalized:
        return url
    if url.startswith(normalized):
        return url
    return f"{normalized}{url}"


def _build_opener(*, verify_ssl: bool, proxy: str | None, use_env_proxy: bool = True):
    handlers = []
    if proxy:
        handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
    elif use_env_proxy:
        handlers.append(ProxyHandler())
    else:
        handlers.append(ProxyHandler({}))

    if verify_ssl:
        handlers.append(HTTPSHandler(context=ssl.create_default_context()))
    else:
        handlers.append(HTTPSHandler(context=ssl._create_unverified_context()))
    return build_opener(*handlers)


def _safe_int(raw_value: str | None) -> int | None:
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _parse_content_range_total(content_range: str | None) -> int | None:
    if not content_range or "/" not in content_range:
        return None
    total = content_range.rsplit("/", 1)[1].strip()
    if total == "*":
        return None
    return _safe_int(total)


def _format_byte_count(byte_count: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(byte_count)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


class _DownloadProgressReporter:
    def __init__(self, label: str, total_bytes: int | None, *, enabled: bool) -> None:
        self.label = label
        self.total_bytes = total_bytes
        self.enabled = enabled
        self._downloaded_bytes = 0
        self._started_at = time.monotonic()
        self._last_emit_at = self._started_at
        self._last_emitted_bytes = -1
        self._finished = False
        self._lock = threading.Lock()

    def update(self, byte_count: int) -> None:
        if not self.enabled or self._finished:
            return
        with self._lock:
            self._downloaded_bytes += byte_count
            self._emit_locked(force=False)

    def finish(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._emit_locked(force=True)
            self._finished = True

    def _emit_locked(self, *, force: bool) -> None:
        now = time.monotonic()
        if not force and (now - self._last_emit_at) < DOWNLOAD_PROGRESS_UPDATE_SECONDS:
            return
        if not force and self._downloaded_bytes == self._last_emitted_bytes:
            return
        if force and self._finished:
            return
        if force and self._downloaded_bytes == self._last_emitted_bytes:
            return

        elapsed = max(now - self._started_at, 0.001)
        speed = self._downloaded_bytes / elapsed
        if self.total_bytes:
            ratio = min(self._downloaded_bytes / self.total_bytes, 1.0)
            filled = min(24, int(ratio * 24))
            bar = "#" * filled + "-" * (24 - filled)
            message = (
                f"{self.label}: [{bar}] {ratio * 100:5.1f}% "
                f"{_format_byte_count(self._downloaded_bytes)}/{_format_byte_count(self.total_bytes)} "
                f"at {_format_byte_count(speed)}/s"
            )
        else:
            message = (
                f"{self.label}: {_format_byte_count(self._downloaded_bytes)} downloaded "
                f"at {_format_byte_count(speed)}/s"
            )
        print(message, flush=True)
        self._last_emit_at = now
        self._last_emitted_bytes = self._downloaded_bytes


def _build_request_headers(*, accept: str, headers: dict[str, str] | None = None) -> dict[str, str]:
    request_headers = {
        "Accept": accept,
        "User-Agent": DOWNLOAD_USER_AGENT,
    }
    if headers:
        request_headers.update(headers)
    return request_headers


def _probe_download_capabilities(
    url: str,
    *,
    verify_ssl: bool,
    proxy: str | None,
    url_proxy_prefix: str | None,
    headers: dict[str, str] | None = None,
) -> tuple[int | None, bool]:
    opener = _build_opener(
        verify_ssl=verify_ssl,
        proxy=proxy,
        use_env_proxy=url_proxy_prefix is None,
    )
    request_headers = _build_request_headers(accept="application/octet-stream", headers=headers)
    request_headers["Range"] = "bytes=0-0"
    request = Request(_apply_url_proxy_prefix(url, url_proxy_prefix), headers=request_headers)
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            total_size = _parse_content_range_total(response.headers.get("Content-Range"))
            if total_size is None:
                total_size = _safe_int(response.headers.get("Content-Length"))
            supports_ranges = response.status == 206 and total_size is not None
            return total_size, supports_ranges
    except (HTTPError, TimeoutError, URLError):
        return None, False


def _plan_download_ranges(total_size: int, download_concurrency: int) -> list[tuple[int, int]]:
    worker_count = max(1, min(download_concurrency, total_size))
    segment_size = math.ceil(total_size / worker_count)
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total_size:
        end = min(start + segment_size - 1, total_size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def _stream_download_to_file(
    url: str,
    destination: Path,
    *,
    verify_ssl: bool,
    proxy: str | None,
    url_proxy_prefix: str | None,
    headers: dict[str, str] | None,
    progress: _DownloadProgressReporter,
) -> None:
    opener = _build_opener(
        verify_ssl=verify_ssl,
        proxy=proxy,
        use_env_proxy=url_proxy_prefix is None,
    )
    request = Request(
        _apply_url_proxy_prefix(url, url_proxy_prefix),
        headers=_build_request_headers(accept="application/octet-stream", headers=headers),
    )
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, destination.open("wb") as handle:
            while True:
                chunk = response.read(DOWNLOAD_BUFFER_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                progress.update(len(chunk))
    except HTTPError as exc:
        raise SkillsBundleError(f"Failed to download {url}: HTTP {exc.code}") from exc
    except TimeoutError as exc:
        raise SkillsBundleError(
            f"Timed out downloading {url} after {DOWNLOAD_TIMEOUT_SECONDS} seconds"
        ) from exc
    except URLError as exc:
        raise SkillsBundleError(f"Failed to download {url}: {exc.reason}") from exc


def _download_range_to_file(
    url: str,
    destination: Path,
    *,
    start: int,
    end: int,
    verify_ssl: bool,
    proxy: str | None,
    url_proxy_prefix: str | None,
    headers: dict[str, str] | None,
    progress: _DownloadProgressReporter,
) -> None:
    opener = _build_opener(
        verify_ssl=verify_ssl,
        proxy=proxy,
        use_env_proxy=url_proxy_prefix is None,
    )
    request_headers = _build_request_headers(accept="application/octet-stream", headers=headers)
    request_headers["Range"] = f"bytes={start}-{end}"
    request = Request(_apply_url_proxy_prefix(url, url_proxy_prefix), headers=request_headers)
    expected_bytes = end - start + 1
    written_bytes = 0
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, destination.open("r+b") as handle:
            if response.status != 206:
                raise SkillsBundleError(
                    f"Expected HTTP 206 for ranged download of {url}, got {response.status}"
                )
            handle.seek(start)
            while True:
                chunk = response.read(DOWNLOAD_BUFFER_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                chunk_length = len(chunk)
                written_bytes += chunk_length
                progress.update(chunk_length)
    except HTTPError as exc:
        raise SkillsBundleError(f"Failed to download {url}: HTTP {exc.code}") from exc
    except TimeoutError as exc:
        raise SkillsBundleError(
            f"Timed out downloading {url} after {DOWNLOAD_TIMEOUT_SECONDS} seconds"
        ) from exc
    except URLError as exc:
        raise SkillsBundleError(f"Failed to download {url}: {exc.reason}") from exc

    if written_bytes != expected_bytes:
        raise SkillsBundleError(
            f"Ranged download for {url} wrote {written_bytes} bytes, expected {expected_bytes}"
        )


def _parallel_download_to_file(
    url: str,
    destination: Path,
    *,
    total_size: int,
    verify_ssl: bool,
    proxy: str | None,
    url_proxy_prefix: str | None,
    headers: dict[str, str] | None,
    download_concurrency: int,
    progress: _DownloadProgressReporter,
) -> None:
    ranges = _plan_download_ranges(total_size, download_concurrency)
    with destination.open("wb") as handle:
        handle.truncate(total_size)
    with ThreadPoolExecutor(max_workers=len(ranges)) as executor:
        futures = [
            executor.submit(
                _download_range_to_file,
                url,
                destination,
                start=start,
                end=end,
                verify_ssl=verify_ssl,
                proxy=proxy,
                url_proxy_prefix=url_proxy_prefix,
                headers=headers,
                progress=progress,
            )
            for start, end in ranges
        ]
        for future in futures:
            future.result()


def _download_file(
    url: str,
    destination: Path,
    *,
    verify_ssl: bool,
    proxy: str | None,
    url_proxy_prefix: str | None,
    headers: dict[str, str] | None = None,
    download_concurrency: int = DOWNLOAD_CONCURRENCY,
    show_progress: bool = False,
) -> None:
    has_authorization_header = any(key.lower() == "authorization" for key in (headers or {}))
    if DOWNLOAD_BACKEND == "curl" and not has_authorization_header:
        _download_file_with_curl(
            url,
            destination,
            verify_ssl=verify_ssl,
            proxy=proxy,
            url_proxy_prefix=url_proxy_prefix,
            headers=headers,
            show_progress=show_progress,
        )
        return

    total_size, supports_ranges = _probe_download_capabilities(
        url,
        verify_ssl=verify_ssl,
        proxy=proxy,
        url_proxy_prefix=url_proxy_prefix,
        headers=headers,
    )
    normalized_concurrency = max(1, download_concurrency)
    use_parallel_download = (
        supports_ranges
        and total_size is not None
        and total_size >= MULTIPART_DOWNLOAD_MIN_BYTES
        and normalized_concurrency > 1
    )
    progress = _DownloadProgressReporter(destination.name, total_size, enabled=show_progress)
    if use_parallel_download:
        if show_progress and total_size is not None:
            print(
                f"{destination.name}: using {normalized_concurrency} download workers for {_format_byte_count(total_size)}",
                flush=True,
            )
        _parallel_download_to_file(
            url,
            destination,
            total_size=total_size,
            verify_ssl=verify_ssl,
            proxy=proxy,
            url_proxy_prefix=url_proxy_prefix,
            headers=headers,
            download_concurrency=normalized_concurrency,
            progress=progress,
        )
    else:
        _stream_download_to_file(
            url,
            destination,
            verify_ssl=verify_ssl,
            proxy=proxy,
            url_proxy_prefix=url_proxy_prefix,
            headers=headers,
            progress=progress,
        )
    progress.finish()


def _download_file_with_curl(
    url: str,
    destination: Path,
    *,
    verify_ssl: bool,
    proxy: str | None,
    url_proxy_prefix: str | None,
    headers: dict[str, str] | None,
    show_progress: bool,
) -> None:
    curl_candidates = ["curl.exe", "curl"] if os.name == "nt" else ["curl"]
    curl_command = shutil.which(curl_candidates[0]) or next(
        (shutil.which(candidate) for candidate in curl_candidates[1:] if shutil.which(candidate)),
        None,
    )
    if not curl_command:
        raise SkillsBundleError("curl is not available for skills download")

    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        curl_command,
        "--fail",
        "--location",
        "--show-error",
        "--output",
        str(destination),
        "--connect-timeout",
        "30",
        "--max-time",
        str(DOWNLOAD_TIMEOUT_SECONDS),
    ]
    if show_progress:
        command.append("--progress-bar")
    else:
        command.append("--silent")
    if not verify_ssl:
        command.append("--insecure")
    if proxy:
        command.extend(["--proxy", proxy])
    if headers:
        for header_name, header_value in headers.items():
            command.extend(["--header", f"{header_name}: {header_value}"])
    command.append(_apply_url_proxy_prefix(url, url_proxy_prefix))

    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=not show_progress,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise SkillsBundleError(
            f"Failed to download {url} with curl: {stderr or f'exit code {exc.returncode}'}"
        )

    if not destination.exists() or destination.stat().st_size == 0:
        stderr = completed.stderr.strip() if completed.stderr else ""
        raise SkillsBundleError(
            f"curl reported success for {url} but did not write {destination.name}: {stderr}"
        )


def _read_json(
    url: str,
    *,
    verify_ssl: bool,
    proxy: str | None,
    url_proxy_prefix: str | None,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    opener = _build_opener(
        verify_ssl=verify_ssl,
        proxy=proxy,
        use_env_proxy=url_proxy_prefix is None,
    )
    request_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": DOWNLOAD_USER_AGENT,
    }
    if headers:
        request_headers.update(headers)
    request = Request(_apply_url_proxy_prefix(url, url_proxy_prefix), headers=request_headers)
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise SkillsBundleError(f"Failed to query {url}: HTTP {exc.code}") from exc
    except TimeoutError as exc:
        raise SkillsBundleError(
            f"Timed out querying {url} after {DOWNLOAD_TIMEOUT_SECONDS} seconds"
        ) from exc
    except URLError as exc:
        raise SkillsBundleError(f"Failed to query {url}: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise SkillsBundleError(f"Unexpected JSON payload from {url}")
    return payload


def _build_github_headers(github_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def _resolve_asset_downloads(
    *,
    resolved,
    verify_ssl: bool,
    proxy: str | None,
    url_proxy_prefix: str | None,
    github_token: str | None,
) -> tuple[dict[str, str], dict[str, str] | None]:
    if not github_token:
        return (
            {
                resolved.skills.artifact_file_name: resolved.skills.artifact_download_url,
                resolved.skills.checksum_file_name: resolved.skills.checksum_download_url,
            },
            None,
        )

    release = _read_json(
        f"https://api.github.com/repos/{resolved.skills.release_repo}/releases/tags/{resolved.skills.release_tag}",
        verify_ssl=verify_ssl,
        proxy=proxy,
        url_proxy_prefix=url_proxy_prefix,
        headers=_build_github_headers(github_token),
    )
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise SkillsBundleError(
            f"Release {resolved.skills.release_tag} in {resolved.skills.release_repo} does not expose any assets"
        )

    asset_urls: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_name = asset.get("name")
        asset_url = asset.get("url")
        if isinstance(asset_name, str) and isinstance(asset_url, str):
            asset_urls[asset_name] = asset_url

    missing_assets = [
        file_name
        for file_name in (resolved.skills.artifact_file_name, resolved.skills.checksum_file_name)
        if file_name not in asset_urls
    ]
    if missing_assets:
        raise SkillsBundleError(
            f"Release {resolved.skills.release_tag} is missing expected assets: {', '.join(missing_assets)}"
        )

    return asset_urls, {
        **_build_github_headers(github_token),
        "Accept": "application/octet-stream",
    }


def _parse_expected_sha256(checksum_text: str, artifact_name: str) -> str:
    for line in checksum_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        checksum = parts[0]
        file_name = parts[-1].lstrip("*")
        if file_name == artifact_name:
            return checksum.lower()
    raise SkillsBundleError(f"Checksum file does not contain an entry for {artifact_name}")


def _read_installed_metadata(skills_root: Path) -> dict[str, object] | None:
    metadata_path = skills_root / LOCAL_METADATA_FILE_NAME
    if not metadata_path.is_file():
        return None
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def _has_skill_descriptors(skills_root: Path) -> bool:
    if not skills_root.is_dir():
        return False
    try:
        for entry in skills_root.iterdir():
            skill_path = entry / "SKILL.md"
            if entry.is_dir() and skill_path.is_file() and not _is_placeholder_skill_descriptor(skill_path):
                return True
    except OSError:
        return False
    return False


def _is_placeholder_skill_descriptor(skill_path: Path) -> bool:
    try:
        text = skill_path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return any(marker in text for marker in PLACEHOLDER_SKILL_MARKERS)


def _is_local_repo_skills_tree(skills_root: Path, source_repo_root: Path) -> bool:
    try:
        return skills_root.resolve() == (source_repo_root / ".github" / "skills").resolve()
    except OSError:
        return False


def _extract_skills_archive(artifact_path: Path, destination_root: Path) -> tuple[dict[str, object], int]:
    manifest: dict[str, object] = {}
    extracted_file_count = 0

    with ZipFile(artifact_path) as archive:
        if BUNDLE_MANIFEST_NAME in archive.namelist():
            loaded_manifest = json.loads(archive.read(BUNDLE_MANIFEST_NAME).decode("utf-8"))
            if isinstance(loaded_manifest, dict):
                manifest = loaded_manifest

        for info in archive.infolist():
            if info.is_dir():
                continue

            archive_path = PurePosixPath(info.filename)
            if archive_path == PurePosixPath(BUNDLE_MANIFEST_NAME):
                continue

            prefix_parts = SKILLS_ARCHIVE_PREFIX.parts
            if archive_path.parts[: len(prefix_parts)] != prefix_parts:
                continue

            relative_parts = archive_path.parts[len(prefix_parts) :]
            if not relative_parts:
                continue

            destination_path = destination_root.joinpath(*relative_parts)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            extracted_file_count += 1

    if extracted_file_count == 0:
        raise SkillsBundleError(f"Archive {artifact_path.name} does not contain a .github/skills tree")
    return manifest, extracted_file_count


def ensure_skills_bundle(
    *,
    source_repo_root: Path | str,
    install_root: Path | str | None = None,
    config_path: Path | str | None = None,
    force: bool = False,
    proxy: str | None = None,
    verify_ssl: bool = True,
    github_token: str | None = None,
    url_proxy_prefix: str | None = None,
    download_concurrency: int | None = DOWNLOAD_CONCURRENCY,
    show_progress: bool = False,
    log=None,
) -> dict[str, object]:
    source_repo_root_path = Path(source_repo_root).resolve()
    install_root_path = Path(install_root).resolve() if install_root is not None else source_repo_root_path
    config_path_resolved = Path(config_path).resolve() if config_path is not None else get_default_config_path().resolve()
    github_token = github_token or os.environ.get("GITHUB_TOKEN") or None
    url_proxy_prefix = _normalize_url_proxy_prefix(
        url_proxy_prefix or os.environ.get(URL_PROXY_PREFIX_ENV_NAME)
    )
    normalized_download_concurrency = max(1, download_concurrency or DOWNLOAD_CONCURRENCY)

    config = load_config(config_path_resolved)
    resolved = build_resolved_config(config)
    skills_root = install_root_path / ".github" / "skills"
    existing_metadata = _read_installed_metadata(skills_root)
    has_skill_descriptors = _has_skill_descriptors(skills_root)
    use_local_repo_skills = _is_local_repo_skills_tree(skills_root, source_repo_root_path) and has_skill_descriptors

    if (
        not force
        and has_skill_descriptors
        and (
            (existing_metadata and existing_metadata.get("artifactVersion") == resolved.skills.artifact_version)
            or use_local_repo_skills
        )
    ):
        return {
            "artifactVersion": resolved.skills.artifact_version,
            "artifactFileName": resolved.skills.artifact_file_name,
            "releaseRepo": resolved.skills.release_repo,
            "releaseTag": resolved.skills.release_tag,
            "skillsRoot": str(skills_root),
            "status": "already-present",
        }

    if log is not None:
        log(f"Downloading skills bundle {resolved.skills.artifact_file_name} from {resolved.skills.artifact_download_url}")

    with tempfile.TemporaryDirectory(prefix="shotwright-skills-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        artifact_path = temp_dir / resolved.skills.artifact_file_name
        checksum_path = temp_dir / resolved.skills.checksum_file_name
        asset_download_urls, download_headers = _resolve_asset_downloads(
            resolved=resolved,
            verify_ssl=verify_ssl,
            proxy=proxy,
            url_proxy_prefix=url_proxy_prefix,
            github_token=github_token,
        )

        _download_file(
            asset_download_urls[resolved.skills.artifact_file_name],
            artifact_path,
            verify_ssl=verify_ssl,
            proxy=proxy,
            url_proxy_prefix=url_proxy_prefix,
            headers=download_headers,
            download_concurrency=normalized_download_concurrency,
            show_progress=show_progress,
        )
        _download_file(
            asset_download_urls[resolved.skills.checksum_file_name],
            checksum_path,
            verify_ssl=verify_ssl,
            proxy=proxy,
            url_proxy_prefix=url_proxy_prefix,
            headers=download_headers,
            download_concurrency=normalized_download_concurrency,
            show_progress=show_progress,
        )

        expected_sha256 = _parse_expected_sha256(checksum_path.read_text(encoding="utf-8"), resolved.skills.artifact_file_name)
        actual_sha256 = _sha256_file(artifact_path)
        if actual_sha256.lower() != expected_sha256.lower():
            raise SkillsBundleError(
                f"Checksum mismatch for {resolved.skills.artifact_file_name}: expected {expected_sha256}, got {actual_sha256}"
            )

        staged_skills_root = temp_dir / "skills"
        manifest, extracted_file_count = _extract_skills_archive(artifact_path, staged_skills_root)

        local_metadata = {
            **manifest,
            "artifactFileName": resolved.skills.artifact_file_name,
            "artifactVersion": resolved.skills.artifact_version,
            "checksumSha256": actual_sha256,
            "checksumFileName": resolved.skills.checksum_file_name,
            "checksumDownloadUrl": resolved.skills.checksum_download_url,
            "artifactDownloadUrl": resolved.skills.artifact_download_url,
            "installedAtUtc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "releaseRepo": resolved.skills.release_repo,
            "releaseTag": resolved.skills.release_tag,
        }
        (staged_skills_root / LOCAL_METADATA_FILE_NAME).write_text(
            json.dumps(local_metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        skills_root.parent.mkdir(parents=True, exist_ok=True)
        if skills_root.exists():
            shutil.rmtree(skills_root)
        shutil.move(str(staged_skills_root), str(skills_root))

    return {
        "artifactVersion": resolved.skills.artifact_version,
        "artifactFileName": resolved.skills.artifact_file_name,
        "checksumFileName": resolved.skills.checksum_file_name,
        "releaseRepo": resolved.skills.release_repo,
        "releaseTag": resolved.skills.release_tag,
        "skillsRoot": str(skills_root),
        "extractedFileCount": extracted_file_count,
        "status": "downloaded",
    }
