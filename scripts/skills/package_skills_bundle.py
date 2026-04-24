from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from skills_bundle import ensure_skills_bundle
from shotwright_config import build_resolved_config, get_default_config_path, load_config


SKIP_DIR_NAMES = {".cache", "__pycache__"}
SKIP_FILE_NAMES = {".DS_Store", "Thumbs.db"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package the .github/skills tree into a versioned zip bundle.")
    parser.add_argument("--config", type=Path, default=get_default_config_path())
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    return parser.parse_args()


def has_skill_descriptors(skills_root: Path) -> bool:
    if not skills_root.is_dir():
        return False
    for entry in skills_root.iterdir():
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            return True
    return False


def iter_bundle_files(skills_root: Path, repo_root: Path) -> list[tuple[Path, str]]:
    bundle_files: list[tuple[Path, str]] = []
    for path in sorted(skills_root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.name in SKIP_FILE_NAMES:
            continue
        bundle_files.append((path, path.relative_to(repo_root).as_posix()))
    return bundle_files


def write_bundle(artifact_path: Path, bundle_files: list[tuple[Path, str]], metadata: dict[str, object]) -> None:
    with ZipFile(artifact_path, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for source_path, archive_name in bundle_files:
            archive.write(source_path, archive_name)
        archive.writestr(
            "skills-bundle-manifest.json",
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        )


def write_sha256_file(artifact_path: Path) -> Path:
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    checksum_path = artifact_path.with_suffix(artifact_path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {artifact_path.name}\n", encoding="utf-8")
    return checksum_path


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    config = load_config(args.config.resolve())
    resolved = build_resolved_config(config)

    skills_root = repo_root / ".github" / "skills"
    if not has_skill_descriptors(skills_root):
        ensure_skills_bundle(
            source_repo_root=repo_root,
            install_root=repo_root,
            config_path=args.config.resolve(),
            log=print,
        )
    if not has_skill_descriptors(skills_root):
        raise SystemExit(f"Skills directory not found after hydration: {skills_root}")

    output_dir = (repo_root / args.output_dir).resolve() if not args.output_dir.is_absolute() else args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = output_dir / resolved.skills.artifact_file_name
    bundle_files = iter_bundle_files(skills_root, repo_root)
    metadata = {
        "artifactFileName": artifact_path.name,
        "artifactVersion": resolved.skills.artifact_version,
        "generatedAtUtc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "releaseTag": resolved.skills.release_tag,
        "repoRelativeRoot": ".github/skills",
        "fileCount": len(bundle_files),
    }

    write_bundle(artifact_path, bundle_files, metadata)
    checksum_path = write_sha256_file(artifact_path)

    print(
        json.dumps(
            {
                **metadata,
                "artifactPath": str(artifact_path),
                "checksumPath": str(checksum_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())