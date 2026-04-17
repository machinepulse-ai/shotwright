from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request


DEFAULT_IMAGE = "mcr.microsoft.com/windows/nanoserver:ltsc2025"
DEFAULT_PROXY = os.environ.get("https_proxy") or os.environ.get("http_proxy") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
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
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Full image reference, for example ghcr.io/liuchangfreeman/shotwright/after-effects-setup:<VER>")
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    parser.add_argument("--platform-os", default="windows")
    parser.add_argument("--platform-arch", default="amd64")
    parser.add_argument("--output-dir", default=r"C:\data\images")
    parser.add_argument("--archive-name", default=None)
    parser.add_argument("--load", action="store_true", help="Run docker load after the archive is created.")
    return parser.parse_args()


def sanitize_name(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


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
    return request.build_opener(*handlers)


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

    def _get_token(self, realm: str, service: str, scope: str) -> str:
        cache_key = (service, scope)
        if cache_key in self.token_cache:
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
            )
            request_headers["Authorization"] = f"Bearer {token}"
            return self.opener.open(request.Request(url, headers=request_headers))

    def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        with self.open(url, headers=headers) as response:
            return response.read()

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        with self.open(url, headers=headers) as response:
            payload = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
        return json.loads(payload.decode("utf-8")), response_headers


def ensure_empty_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def choose_manifest(manifest_list: dict[str, Any], platform_os: str, platform_arch: str) -> dict[str, Any]:
    for manifest in manifest_list.get("manifests", []):
        platform = manifest.get("platform", {})
        if platform.get("os") == platform_os and platform.get("architecture") == platform_arch:
            return manifest
    raise RuntimeError(f"No matching manifest found for os={platform_os} arch={platform_arch}")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")


def stream_layer(
    client: RegistryClient,
    blob_url: str,
    headers: dict[str, str],
    compressed_digest: str,
    expected_diff_id: str,
    target_path: Path,
    layer_label: str,
    compressed: bool,
) -> None:
    with client.open(blob_url, headers=headers) as response:
        compressed_hasher = hashlib.sha256()
        wrapped = HashingReader(response, compressed_hasher)
        source = gzip.GzipFile(fileobj=wrapped) if compressed else wrapped
        uncompressed_hasher = hashlib.sha256()
        bytes_written = 0
        last_log = time.monotonic()

        with target_path.open("wb") as out_handle:
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
                    print(f"[{layer_label}] wrote {gib:.2f} GiB", flush=True)
                    last_log = now

    actual_compressed_digest = compressed_hasher.hexdigest()
    actual_diff_id = uncompressed_hasher.hexdigest()
    expected_compressed = compressed_digest.split(":", 1)[1]
    expected_uncompressed = expected_diff_id.split(":", 1)[1]

    if actual_compressed_digest != expected_compressed:
        raise RuntimeError(
            f"Compressed digest mismatch for {layer_label}: expected {expected_compressed} got {actual_compressed_digest}"
        )
    if actual_diff_id != expected_uncompressed:
        raise RuntimeError(
            f"Diff ID mismatch for {layer_label}: expected {expected_uncompressed} got {actual_diff_id}"
        )


def add_file_to_tar(tar: tarfile.TarFile, base_dir: Path, file_path: Path) -> None:
    arcname = file_path.relative_to(base_dir).as_posix()
    tar.add(file_path, arcname=arcname, recursive=False)


def pack_archive(staging_dir: Path, archive_path: Path) -> None:
    if archive_path.exists():
        archive_path.unlink()

    with tarfile.open(archive_path, mode="w", format=tarfile.PAX_FORMAT) as tar:
        add_file_to_tar(tar, staging_dir, staging_dir / "manifest.json")
        add_file_to_tar(tar, staging_dir, staging_dir / "repositories")

        for path in sorted(staging_dir.iterdir()):
            if path.name in {"manifest.json", "repositories"}:
                continue
            if path.is_file():
                add_file_to_tar(tar, staging_dir, path)
                continue

            for root, _, files in os.walk(path):
                root_path = Path(root)
                for file_name in sorted(files):
                    add_file_to_tar(tar, staging_dir, root_path / file_name)


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
    opener = build_opener(args.proxy)
    client = RegistryClient(registry=registry, repository=repository, opener=opener)

    image_name = f"{registry}/{repository}:{reference}"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    archive_name = args.archive_name or f"{sanitize_name(image_name)}_docker.tar"
    staging_dir = output_dir / f"{sanitize_name(image_name)}_staging"
    archive_path = output_dir / archive_name
    ensure_empty_dir(staging_dir)

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

    diff_ids = config.get("rootfs", {}).get("diff_ids", [])
    layers = manifest_payload.get("layers", [])
    if len(diff_ids) != len(layers):
        raise RuntimeError("Layer count mismatch between config diff_ids and manifest layers")

    config_file_name = f"{config_digest.split(':', 1)[1]}.json"
    (staging_dir / config_file_name).write_bytes(config_bytes)

    layer_ids: list[str] = []
    parent_id: str | None = None
    for index, (layer_info, diff_id) in enumerate(zip(layers, diff_ids), start=1):
        layer_id = diff_id.split(":", 1)[1]
        layer_ids.append(layer_id)
        layer_dir = staging_dir / layer_id
        layer_dir.mkdir(parents=True, exist_ok=True)
        layer_tar = layer_dir / "layer.tar"

        blob_url = layer_info.get("urls", [None])[0]
        if not blob_url:
            blob_url = f"{client.base_url}/v2/{repository}/blobs/{layer_info['digest']}"

        print(f"[layer {index}/{len(layers)}] downloading {layer_info['digest']}", flush=True)
        stream_layer(
            client=client,
            blob_url=blob_url,
            headers={},
            compressed_digest=layer_info["digest"],
            expected_diff_id=diff_id,
            target_path=layer_tar,
            layer_label=f"layer {index}/{len(layers)}",
            compressed=layer_info.get("mediaType", "").endswith("+gzip") or layer_info.get("mediaType", "").endswith(".tar.gzip"),
        )

        (layer_dir / "VERSION").write_text("1.0\n", encoding="ascii")
        layer_json: dict[str, Any] = {"id": layer_id}
        if parent_id:
            layer_json["parent"] = parent_id
        created = config.get("created")
        if created:
            layer_json["created"] = created
        write_json(layer_dir / "json", layer_json)
        parent_id = layer_id

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
    pack_archive(staging_dir, archive_path)
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