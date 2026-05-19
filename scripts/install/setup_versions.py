from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_IMAGE_REPOSITORY = "ghcr.io/freeman-mp/shotwright/after-effects-setup"


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _derive_release_year(version: str) -> int:
    major_text = version.split(".", 1)[0].strip()
    try:
        major_version = int(major_text)
    except ValueError as exc:
        raise RuntimeError(f"Unsupported After Effects version for install path derivation: {version}") from exc

    if major_version < 10:
        raise RuntimeError(f"Unexpected After Effects major version for install path derivation: {version}")

    return 2000 + major_version


def _resolve_install_dir_name(version: str, selected: dict[str, str]) -> str:
    configured_name = selected.get("install-dir-name", "").strip()
    if configured_name:
        return configured_name
    return f"Adobe After Effects {_derive_release_year(version)}"


def parse_setup_versions(config_path: Path) -> dict[str, object]:
    current: str | None = None
    image_repository = DEFAULT_IMAGE_REPOSITORY
    versions: dict[str, dict[str, str]] = {}
    current_version_key: str | None = None
    in_versions = False

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            current_version_key = None
            if stripped == "versions:":
                in_versions = True
                continue

            key, separator, value = stripped.partition(":")
            if separator != ":":
                raise RuntimeError(f"Unsupported setup-versions line: {raw_line}")

            key = key.strip()
            value = _strip_quotes(value)
            if key == "current":
                current = value
            elif key == "image-repository":
                image_repository = value or DEFAULT_IMAGE_REPOSITORY
            else:
                raise RuntimeError(f"Unsupported top-level key in setup-versions.yml: {key}")
            continue

        if not in_versions:
            raise RuntimeError("Found indented content before versions: block in setup-versions.yml")

        if indent == 2 and stripped.endswith(":"):
            current_version_key = _strip_quotes(stripped[:-1])
            versions[current_version_key] = {}
            continue

        if indent == 4 and current_version_key is not None:
            key, separator, value = stripped.partition(":")
            if separator != ":":
                raise RuntimeError(f"Unsupported version mapping line: {raw_line}")
            versions[current_version_key][key.strip()] = _strip_quotes(value)
            continue

        raise RuntimeError(f"Unsupported indentation in setup-versions.yml: {raw_line}")

    if not current:
        raise RuntimeError("Missing current version in setup-versions.yml")
    if current not in versions:
        raise RuntimeError(f"Current version {current} not found in setup-versions.yml")

    selected = versions[current]
    product_id = selected.get("product-id", "AEFT")
    platform = selected.get("platform", "win64")
    payload_dir_name = f"{product_id}_{current}_{platform}"
    helper_dir_name = f"CreativeCloudHelper_{platform}"
    install_dir_name = _resolve_install_dir_name(current, selected)
    release_year = _derive_release_year(current)

    return {
        "current": current,
        "product_id": product_id,
        "platform": platform,
        "release_year": release_year,
        "payload_dir_name": payload_dir_name,
        "helper_dir_name": helper_dir_name,
        "install_dir_name": install_dir_name,
        "install_root": f"C:\\Program Files\\Adobe\\{install_dir_name}",
        "image_repository": image_repository,
        "ghcr_image": f"{image_repository}:{current}",
        "versions": versions,
    }


def get_default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "setup-versions.yml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve the active Shotwright setup version from setup-versions.yml."
    )
    parser.add_argument("--config", type=Path, default=get_default_config_path())
    parser.add_argument(
        "--field",
        choices=[
            "current",
            "product_id",
            "platform",
            "release_year",
            "payload_dir_name",
            "helper_dir_name",
            "install_dir_name",
            "install_root",
            "image_repository",
            "ghcr_image",
        ],
        default=None,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = parse_setup_versions(args.config.resolve())

    if args.field:
        print(data[args.field])
    else:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
