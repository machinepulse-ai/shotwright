from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from shotwright_config import build_resolved_config, get_default_config_path, load_config


_RESOLVED = build_resolved_config(load_config(get_default_config_path()))
DEFAULT_OUTPUT_DIR = _RESOLVED.host.image_archive_root

DEFAULT_PROXY = os.environ.get("https_proxy") or os.environ.get("http_proxy") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
DEFAULT_DOWNLOAD_JOBS = 4
CHUNK_SIZE = 32 * 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 10
MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


@dataclass(frozen=True)
class LayerPlan:
    index: int
    total: int
    layer_id: str
    blob_url: str
    compressed_digest: str
    diff_id: str
    media_type: str
    compressed: bool

    @property
    def label(self) -> str:
        return f"layer {self.index}/{self.total}"


class HashingReader:
    def __init__(self, wrapped, hasher: hashlib._hashlib.HASH) -> None:
        self._wrapped = wrapped
        self._hasher = hasher

    def read(self, size: int = -1) -> bytes:
        data = self._wrapped.read(size)
        if data:
            self._hasher.update(data)
        return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a container image through an HTTP proxy, package it as a docker archive, and optionally docker load it locally."
    )
    parser.add_argument("--image", required=True, help="Full image reference, for example ghcr.io/machinepulse-ai/shotwright/after-effects-setup:<VER>")
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    parser.add_argument("--platform-os", default="windows")
    parser.add_argument("--platform-arch", default="amd64")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--archive-name", default=None)
    parser.add_argument("--jobs", type=int, default=DEFAULT_DOWNLOAD_JOBS, help="Concurrent layer download workers. Default: 4.")
    parser.add_argument("--load", action="store_true", help="Run docker load after the archive is created.")
    return parser.parse_args()


def sanitize_name(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


def digest_hex(digest: str) -> str:
    return digest.split(":", 1)[1]


def parse_image_reference(image: str) -> tuple[str, str, str, str]:
    reference = image.strip()
    if not reference:
        raise RuntimeError("Image reference is required.")

    if "@" in reference:
        name, _, tag = reference.partition("@")
        reference_type = "digest"
    else:
        slash = reference.rfind("/")
        colon = reference.rfind(":")
        if colon > slash:
            name = reference[:colon]
            tag = reference[colon + 1 :]
        else:
            name = reference
            tag = "latest"
        reference_type = "tag"

    parts = name.split("/")
    if len(parts) == 1 or ("." not in parts[0] and ":" not in parts[0] and parts[0] != "localhost"):
        registry = "registry-1.docker.io"
        repository = "/".join((["library"] if len(parts) == 1 else []) + parts)
    else:
        registry = parts[0]
        repository = "/".join(parts[1:])

    if not repository:
        raise RuntimeError(f"Invalid image reference: {image}")

    return registry, repository, tag, reference_type


def build_opener(proxy_url: str | None) -> request.OpenerDirector:
    handlers = []
    if proxy_url:
        handlers.append(request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    else:
        handlers.append(request.ProxyHandler({}))
    handlers.append(RegistryRedirectHandler())
    return request.build_opener(*handlers)


def build_registry_client(registry: str, repository: str, proxy_url: str | None) -> "RegistryClient":
    return RegistryClient(registry=registry, repository=repository, opener=build_opener(proxy_url))


def remove_header_case_insensitive(headers: dict[str, str], header_name: str) -> None:
    for existing_name in list(headers):
        if existing_name.lower() == header_name.lower():
            headers.pop(existing_name, None)


class RegistryRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None

        source = parse.urlsplit(req.full_url)
        target = parse.urlsplit(newurl)
        if source.netloc.lower() != target.netloc.lower():
            remove_header_case_insensitive(redirected.headers, "Authorization")
            remove_header_case_insensitive(redirected.unredirected_hdrs, "Authorization")

        return redirected


def parse_www_authenticate(header_value: str) -> tuple[str, dict[str, str]]:
    scheme, _, remainder = header_value.partition(" ")
    attributes: dict[str, str] = {}
    for part in remainder.split(","):
        key, separator, value = part.strip().partition("=")
        if separator != "=":
            continue
        attributes[key] = value.strip().strip('"')
    return scheme, attributes


class RegistryClient:
    def __init__(self, registry: str, repository: str, opener: request.OpenerDirector) -> None:
        self.registry = registry
        self.repository = repository
        self.opener = opener
        self.base_url = f"https://{registry}"
        self.token_cache: dict[tuple[str, str], str] = {}

    def _get_token(self, realm: str, service: str, scope: str, force_refresh: bool = False) -> str:
        cache_key = (service, scope)
        if not force_refresh and cache_key in self.token_cache:
            return self.token_cache[cache_key]

        query = {"service": service}
        if scope:
            query["scope"] = scope
        token_url = f"{realm}?{parse.urlencode(query)}"
        with self.opener.open(request.Request(token_url)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        token = payload.get("token") or payload.get("access_token")
        if not token:
            raise RuntimeError(f"Authentication token missing from {realm}")
        self.token_cache[cache_key] = token
        return token

    def open(self, url: str, headers: dict[str, str] | None = None):
        request_headers = dict(headers or {})
        for _ in range(3):
            req = request.Request(url, headers=request_headers)
            try:
                return self.opener.open(req)
            except error.HTTPError as exc:
                if exc.code != 401:
                    raise

                auth_header = exc.headers.get("WWW-Authenticate")
                if not auth_header:
                    raise

                scheme, attributes = parse_www_authenticate(auth_header)
                if scheme.lower() != "bearer":
                    raise RuntimeError(f"Unsupported registry auth scheme: {scheme}")

                token = self._get_token(
                    realm=attributes["realm"],
                    service=attributes.get("service", self.registry),
                    scope=attributes.get("scope", f"repository:{self.repository}:pull"),
                    force_refresh=True,
                )
                request_headers["Authorization"] = f"Bearer {token}"

        raise RuntimeError(f"Registry authentication failed after retries: {url}")

    def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        with self.open(url, headers=headers) as response:
            return response.read()

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        with self.open(url, headers=headers) as response:
            payload = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
        return json.loads(payload.decode("utf-8")), response_headers


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def choose_manifest(manifest_list: dict[str, Any], platform_os: str, platform_arch: str) -> dict[str, Any]:
    for manifest in manifest_list.get("manifests", []):
        platform = manifest.get("platform", {})
        if platform.get("os") == platform_os and platform.get("architecture") == platform_arch:
            return manifest
    raise RuntimeError(f"No matching manifest found for os={platform_os} arch={platform_arch}")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def add_file_to_tar(tar: tarfile.TarFile, base_dir: Path, file_path: Path) -> None:
    arcname = file_path.relative_to(base_dir).as_posix()
    tar.add(file_path, arcname=arcname, recursive=False)


def resolve_total_size(headers, status_code: int, existing_size: int) -> int | None:
    content_range = headers.get("Content-Range") or headers.get("content-range")
    if content_range:
        _, _, total_text = content_range.partition("/")
        if total_text.isdigit():
            return int(total_text)

    content_length = headers.get("Content-Length") or headers.get("content-length")
    if content_length and content_length.isdigit():
        reported = int(content_length)
        if status_code == 206 and existing_size > 0:
            return existing_size + reported
        return reported

    return None


def format_progress(current_bytes: int, total_bytes: int | None) -> str:
    gib = current_bytes / (1024 ** 3)
    if total_bytes and total_bytes > 0:
        percent = current_bytes / total_bytes * 100
        total_gib = total_bytes / (1024 ** 3)
        return f"{gib:.2f}/{total_gib:.2f} GiB ({percent:.1f}%)"
    return f"{gib:.2f} GiB"


def verify_completed_layer(layer_dir: Path, plan: LayerPlan) -> bool:
    marker_path = layer_dir / ".layer.complete.json"
    layer_tar = layer_dir / "layer.tar"
    if not marker_path.exists() or not layer_tar.exists():
        return False

    try:
        metadata = read_json(marker_path)
    except (json.JSONDecodeError, OSError):
        return False

    return metadata.get("compressed_digest") == plan.compressed_digest and metadata.get("diff_id") == plan.diff_id


def download_blob(
    plan: LayerPlan,
    layer_dir: Path,
    client_factory: Callable[[], RegistryClient],
) -> Path:
    expected_compressed = digest_hex(plan.compressed_digest)
    blob_path = layer_dir / "layer.blob"
    partial_path = layer_dir / "layer.blob.part"

    if blob_path.exists():
        print(f"[{plan.label}] verifying cached blob {plan.compressed_digest}", flush=True)
        if compute_sha256(blob_path) == expected_compressed:
            print(f"[{plan.label}] reusing cached blob {plan.compressed_digest}", flush=True)
            return blob_path
        blob_path.unlink()

    attempts = 0
    while attempts < 2:
        attempts += 1
        existing_size = partial_path.stat().st_size if partial_path.exists() else 0
        hasher = hashlib.sha256()
        if existing_size > 0:
            print(f"[{plan.label}] resuming blob at {format_progress(existing_size, None)}", flush=True)
            with partial_path.open("rb") as existing_handle:
                while True:
                    chunk = existing_handle.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    hasher.update(chunk)

        headers: dict[str, str] = {}
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

        client = client_factory()
        try:
            response = client.open(plan.blob_url, headers=headers)
        except error.HTTPError as exc:
            if exc.code == 416 and partial_path.exists():
                if compute_sha256(partial_path) != expected_compressed:
                    partial_path.unlink(missing_ok=True)
                    raise RuntimeError(f"[{plan.label}] cached partial blob is invalid and cannot be resumed") from exc
                partial_path.replace(blob_path)
                return blob_path
            raise

        with response:
            status_code = response.getcode() or 200
            if existing_size > 0 and status_code != 206:
                print(f"[{plan.label}] registry ignored range request, restarting blob download", flush=True)
                partial_path.unlink(missing_ok=True)
                continue

            if status_code == 206 and existing_size > 0:
                mode = "ab"
                bytes_written = existing_size
            else:
                mode = "wb"
                bytes_written = 0
                hasher = hashlib.sha256()

            total_bytes = resolve_total_size(response.headers, status_code, bytes_written)
            last_log = time.monotonic()

            with partial_path.open(mode) as out_handle:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out_handle.write(chunk)
                    hasher.update(chunk)
                    bytes_written += len(chunk)

                    now = time.monotonic()
                    if now - last_log >= PROGRESS_INTERVAL_SECONDS:
                        print(f"[{plan.label}] downloaded {format_progress(bytes_written, total_bytes)}", flush=True)
                        last_log = now

        actual_compressed = hasher.hexdigest()
        if actual_compressed != expected_compressed:
            partial_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Compressed digest mismatch for {plan.label}: expected {expected_compressed} got {actual_compressed}"
            )

        partial_path.replace(blob_path)
        print(f"[{plan.label}] blob ready {plan.compressed_digest}", flush=True)
        return blob_path

    raise RuntimeError(f"[{plan.label}] failed to resume blob download after retries")


def materialize_layer(plan: LayerPlan, layer_dir: Path) -> None:
    if verify_completed_layer(layer_dir, plan):
        print(f"[{plan.label}] reusing materialized layer {plan.layer_id}", flush=True)
        return

    blob_path = layer_dir / "layer.blob"
    layer_tar = layer_dir / "layer.tar"
    marker_path = layer_dir / ".layer.complete.json"
    expected_compressed = digest_hex(plan.compressed_digest)
    expected_diff_id = digest_hex(plan.diff_id)

    compressed_hasher = hashlib.sha256()
    uncompressed_hasher = hashlib.sha256()
    bytes_written = 0
    last_log = time.monotonic()

    with blob_path.open("rb") as blob_handle:
        wrapped = HashingReader(blob_handle, compressed_hasher)
        source = gzip.GzipFile(fileobj=wrapped) if plan.compressed else wrapped

        with layer_tar.open("wb") as out_handle:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                out_handle.write(chunk)
                uncompressed_hasher.update(chunk)
                bytes_written += len(chunk)

                now = time.monotonic()
                if now - last_log >= PROGRESS_INTERVAL_SECONDS:
                    gib = bytes_written / (1024 ** 3)
                    print(f"[{plan.label}] materialized {gib:.2f} GiB", flush=True)
                    last_log = now

    actual_compressed = compressed_hasher.hexdigest()
    actual_diff_id = uncompressed_hasher.hexdigest()
    if actual_compressed != expected_compressed:
        layer_tar.unlink(missing_ok=True)
        raise RuntimeError(
            f"Compressed digest mismatch for {plan.label}: expected {expected_compressed} got {actual_compressed}"
        )
    if actual_diff_id != expected_diff_id:
        layer_tar.unlink(missing_ok=True)
        raise RuntimeError(
            f"Diff ID mismatch for {plan.label}: expected {expected_diff_id} got {actual_diff_id}"
        )

    write_json(marker_path, {"compressed_digest": plan.compressed_digest, "diff_id": plan.diff_id})


def write_layer_files(layer_dir: Path, layer_id: str, parent_id: str | None, created: str | None) -> None:
    (layer_dir / "VERSION").write_text("1.0\n", encoding="ascii")
    layer_json: dict[str, Any] = {"id": layer_id}
    if parent_id:
        layer_json["parent"] = parent_id
    if created:
        layer_json["created"] = created
    write_json(layer_dir / "json", layer_json)


def pack_archive(staging_dir: Path, archive_path: Path, config_file_name: str, layer_ids: list[str]) -> None:
    if archive_path.exists():
        archive_path.unlink()

    with tarfile.open(archive_path, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for file_name in ["manifest.json", "repositories", config_file_name]:
            add_file_to_tar(tar, staging_dir, staging_dir / file_name)
        for layer_id in layer_ids:
            for file_name in ["VERSION", "json", "layer.tar"]:
                add_file_to_tar(tar, staging_dir, staging_dir / layer_id / file_name)


def maybe_load_archive(archive_path: Path) -> None:
    result = subprocess.run(
        ["docker", "load", "-i", str(archive_path)],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "docker load failed")
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)


def main() -> int:
    args = parse_args()
    registry, repository, reference, _ = parse_image_reference(args.image)
    client = build_registry_client(registry=registry, repository=repository, proxy_url=args.proxy)

    image_name = f"{registry}/{repository}:{reference}"
    output_dir = Path(args.output_dir)
    ensure_dir(output_dir)

    manifest_url = f"{client.base_url}/v2/{repository}/manifests/{reference}"
    manifest_payload, manifest_headers = client.get_json(
        manifest_url,
        headers={"Accept": MANIFEST_ACCEPT},
    )

    if manifest_payload.get("schemaVersion") != 2:
        raise RuntimeError("Unsupported manifest schema version")

    if "manifests" in manifest_payload:
        selected = choose_manifest(manifest_payload, args.platform_os, args.platform_arch)
        selected_digest = selected["digest"]
        print(f"Selected manifest {selected_digest}", flush=True)
        manifest_payload, manifest_headers = client.get_json(
            f"{client.base_url}/v2/{repository}/manifests/{selected_digest}",
            headers={"Accept": MANIFEST_ACCEPT},
        )

    config_digest = manifest_payload["config"]["digest"]
    config_url = f"{client.base_url}/v2/{repository}/blobs/{config_digest}"
    config_bytes = client.get_bytes(config_url)
    config = json.loads(config_bytes.decode("utf-8"))

    manifest_digest = manifest_headers.get("docker-content-digest", config_digest)
    archive_name = args.archive_name or f"{sanitize_name(image_name)}_docker.tar"
    staging_dir = output_dir / f"{sanitize_name(image_name)}_{digest_hex(manifest_digest)}_staging"
    archive_path = output_dir / archive_name
    ensure_dir(staging_dir)

    diff_ids = config.get("rootfs", {}).get("diff_ids", [])
    layers = manifest_payload.get("layers", [])
    if len(diff_ids) != len(layers):
        raise RuntimeError("Layer count mismatch between config diff_ids and manifest layers")

    config_file_name = f"{digest_hex(config_digest)}.json"
    (staging_dir / config_file_name).write_bytes(config_bytes)
    write_json(
        staging_dir / ".staging.json",
        {
            "image": image_name,
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
        },
    )

    layer_plans: list[LayerPlan] = []
    for index, (layer_info, diff_id) in enumerate(zip(layers, diff_ids), start=1):
        blob_url = layer_info.get("urls", [None])[0]
        if not blob_url:
            blob_url = f"{client.base_url}/v2/{repository}/blobs/{layer_info['digest']}"

        plan = LayerPlan(
            index=index,
            total=len(layers),
            layer_id=digest_hex(diff_id),
            blob_url=blob_url,
            compressed_digest=layer_info["digest"],
            diff_id=diff_id,
            media_type=layer_info.get("mediaType", ""),
            compressed=layer_info.get("mediaType", "").endswith("+gzip") or layer_info.get("mediaType", "").endswith(".tar.gzip"),
        )
        layer_plans.append(plan)
        ensure_dir(staging_dir / plan.layer_id)

    jobs = max(1, min(args.jobs, len(layer_plans) if layer_plans else 1))
    print(f"Downloading {len(layer_plans)} layers with {jobs} worker(s)", flush=True)
    client_factory = lambda: build_registry_client(registry=registry, repository=repository, proxy_url=args.proxy)
    future_map: dict[concurrent.futures.Future[Path], LayerPlan] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        for plan in layer_plans:
            layer_dir = staging_dir / plan.layer_id
            future = executor.submit(download_blob, plan, layer_dir, client_factory)
            future_map[future] = plan

        for future in concurrent.futures.as_completed(future_map):
            plan = future_map[future]
            future.result()
            print(f"[{plan.label}] download complete", flush=True)

    layer_ids: list[str] = []
    parent_id: str | None = None
    created = config.get("created")
    for plan in layer_plans:
        layer_ids.append(plan.layer_id)
        layer_dir = staging_dir / plan.layer_id
        materialize_layer(plan, layer_dir)
        write_layer_files(layer_dir, plan.layer_id, parent_id, created)
        parent_id = plan.layer_id

    write_json(
        staging_dir / "manifest.json",
        [
            {
                "Config": config_file_name,
                "RepoTags": [image_name],
                "Layers": [f"{layer_id}/layer.tar" for layer_id in layer_ids],
            }
        ],
    )
    write_json(
        staging_dir / "repositories",
        {f"{registry}/{repository}": {reference: layer_ids[-1]}},
    )

    print(f"Packing {archive_path}", flush=True)
    pack_archive(staging_dir, archive_path, config_file_name, layer_ids)
    archive_size_gib = archive_path.stat().st_size / (1024 ** 3)
    print(f"Archive ready: {archive_path} ({archive_size_gib:.2f} GiB)", flush=True)

    if args.load:
        print(f"Loading {archive_path} into Docker", flush=True)
        maybe_load_archive(archive_path)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
