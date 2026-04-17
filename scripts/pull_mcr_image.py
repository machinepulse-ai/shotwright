from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path
from typing import Any
from urllib import request


DEFAULT_PROXY = "http://192.168.1.80:8080"
DEFAULT_REGISTRY = "https://mcr.microsoft.com"
DEFAULT_REPOSITORY = "windows/server"
DEFAULT_TAG = "ltsc2025"
CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 10


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
        description="Pull an image from MCR over HTTP and package it as a docker save archive."
    )
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--tag", default=DEFAULT_TAG)
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    parser.add_argument("--platform-os", default="windows")
    parser.add_argument("--platform-arch", default="amd64")
    parser.add_argument("--output-dir", default=r"D:\pve")
    parser.add_argument(
        "--archive-name",
        default="mcr.microsoft.com_windows_server_ltsc2025_docker.tar",
    )
    return parser.parse_args()


def build_opener(proxy_url: str | None) -> request.OpenerDirector:
    handlers = []
    if proxy_url:
        handlers.append(request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    else:
        handlers.append(request.ProxyHandler({}))
    return request.build_opener(*handlers)


def http_get_bytes(
    opener: request.OpenerDirector,
    url: str,
    headers: dict[str, str] | None = None,
) -> bytes:
    req = request.Request(url, headers=headers or {})
    with opener.open(req) as response:
        return response.read()


def http_get_json(
    opener: request.OpenerDirector,
    url: str,
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    req = request.Request(url, headers=headers or {})
    with opener.open(req) as response:
        data = response.read()
        response_headers = {key.lower(): value for key, value in response.headers.items()}
    return json.loads(data.decode("utf-8")), response_headers


def ensure_empty_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def choose_manifest(
    manifest_list: dict[str, Any],
    platform_os: str,
    platform_arch: str,
) -> dict[str, Any]:
    for manifest in manifest_list.get("manifests", []):
        platform = manifest.get("platform", {})
        if platform.get("os") == platform_os and platform.get("architecture") == platform_arch:
            return manifest
    raise RuntimeError(
        "No matching manifest found for os=%s arch=%s" % (platform_os, platform_arch)
    )


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")


def stream_blob_to_layer_tar(
    opener: request.OpenerDirector,
    blob_url: str,
    headers: dict[str, str],
    compressed_digest: str,
    expected_diff_id: str,
    target_path: Path,
    layer_label: str,
) -> None:
    req = request.Request(blob_url, headers=headers)
    with opener.open(req) as response:
        compressed_hasher = hashlib.sha256()
        wrapped = HashingReader(response, compressed_hasher)
        gunzip = gzip.GzipFile(fileobj=wrapped)
        uncompressed_hasher = hashlib.sha256()
        bytes_written = 0
        last_log = time.monotonic()

        with target_path.open("wb") as out_handle:
            while True:
                chunk = gunzip.read(CHUNK_SIZE)
                if not chunk:
                    break
                out_handle.write(chunk)
                uncompressed_hasher.update(chunk)
                bytes_written += len(chunk)

                now = time.monotonic()
                if now - last_log >= PROGRESS_INTERVAL_SECONDS:
                    gib = bytes_written / (1024 ** 3)
                    print("[%s] wrote %.2f GiB" % (layer_label, gib), flush=True)
                    last_log = now

    actual_compressed_digest = compressed_hasher.hexdigest()
    actual_diff_id = uncompressed_hasher.hexdigest()
    expected_compressed = compressed_digest.split(":", 1)[1]
    expected_uncompressed = expected_diff_id.split(":", 1)[1]

    if actual_compressed_digest != expected_compressed:
        raise RuntimeError(
            "Compressed digest mismatch for %s: expected %s got %s"
            % (layer_label, expected_compressed, actual_compressed_digest)
        )
    if actual_diff_id != expected_uncompressed:
        raise RuntimeError(
            "Diff ID mismatch for %s: expected %s got %s"
            % (layer_label, expected_uncompressed, actual_diff_id)
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


def main() -> int:
    args = parse_args()
    opener = build_opener(args.proxy)

    image_name = "%s/%s:%s" % (
        args.registry.replace("https://", "").replace("http://", ""),
        args.repository,
        args.tag,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = output_dir / "mcr.microsoft.com_windows_server_ltsc2025_staging"
    ensure_empty_dir(staging_dir)
    archive_path = output_dir / args.archive_name

    manifest_list_url = "%s/v2/%s/manifests/%s" % (args.registry, args.repository, args.tag)
    manifest_list, manifest_list_headers = http_get_json(
        opener,
        manifest_list_url,
        headers={
            "Accept": "application/vnd.docker.distribution.manifest.list.v2+json"
        },
    )

    if manifest_list.get("schemaVersion") != 2:
        raise RuntimeError("Unsupported manifest list schema version")

    selected = choose_manifest(manifest_list, args.platform_os, args.platform_arch)
    selected_digest = selected["digest"]
    print("Selected manifest %s" % selected_digest, flush=True)

    manifest_url = "%s/v2/%s/manifests/%s" % (args.registry, args.repository, selected_digest)
    manifest, _ = http_get_json(
        opener,
        manifest_url,
        headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
    )

    config_digest = manifest["config"]["digest"]
    config_url = "%s/v2/%s/blobs/%s" % (args.registry, args.repository, config_digest)
    config_bytes = http_get_bytes(opener, config_url)
    config = json.loads(config_bytes.decode("utf-8"))

    diff_ids = config.get("rootfs", {}).get("diff_ids", [])
    layers = manifest.get("layers", [])
    if len(diff_ids) != len(layers):
        raise RuntimeError(
            "Layer count mismatch between config diff_ids and manifest layers"
        )

    config_file_name = "%s.json" % config_digest.split(":", 1)[1]
    (staging_dir / config_file_name).write_bytes(config_bytes)

    layer_ids: list[str] = []
    parent_id: str | None = None
    for index, (layer_info, diff_id) in enumerate(zip(layers, diff_ids), start=1):
        layer_id = diff_id.split(":", 1)[1]
        layer_ids.append(layer_id)
        layer_dir = staging_dir / layer_id
        layer_dir.mkdir(parents=True, exist_ok=True)
        layer_tar = layer_dir / "layer.tar"

        if layer_tar.exists() and sha256_file(layer_tar) == layer_id:
            print("[layer %d/%d] reusing existing layer.tar" % (index, len(layers)), flush=True)
        else:
            if layer_tar.exists():
                layer_tar.unlink()
            print(
                "[layer %d/%d] downloading %s" % (index, len(layers), layer_info["digest"]),
                flush=True,
            )
            blob_url = "%s/v2/%s/blobs/%s" % (
                args.registry,
                args.repository,
                layer_info["digest"],
            )
            stream_blob_to_layer_tar(
                opener,
                blob_url,
                headers={},
                compressed_digest=layer_info["digest"],
                expected_diff_id=diff_id,
                target_path=layer_tar,
                layer_label="layer %d/%d" % (index, len(layers)),
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
                "Layers": ["%s/layer.tar" % layer_id for layer_id in layer_ids],
            }
        ],
    )
    write_json(
        staging_dir / "repositories",
        {args.registry.replace("https://", "").replace("http://", "") + "/" + args.repository: {args.tag: layer_ids[-1]}},
    )

    print("Packing %s" % archive_path, flush=True)
    pack_archive(staging_dir, archive_path)
    archive_size_gib = archive_path.stat().st_size / (1024 ** 3)
    print("Archive ready: %s (%.2f GiB)" % (archive_path, archive_size_gib), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)