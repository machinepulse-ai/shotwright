"""Install project dependencies from pyproject.toml without installing project sources.

This keeps Docker dependency layers stable when application source files change.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pyproject", type=Path, help="Path to pyproject.toml")
    parser.add_argument("--extra", action="append", default=[], help="Optional dependency group to install")
    parser.add_argument("--index-url", default="", help="Optional package index URL")
    return parser.parse_args()


def load_dependencies(pyproject_path: Path, extras: list[str]) -> list[str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project") or {}
    dependencies = list(project.get("dependencies") or [])

    optional_dependencies = project.get("optional-dependencies") or {}
    for extra in extras:
        dependencies.extend(optional_dependencies.get(extra) or [])

    return dependencies


def main() -> int:
    args = parse_args()
    dependencies = load_dependencies(args.pyproject, args.extra)
    if not dependencies:
        return 0

    command = [
        sys.executable,
        "-m",
        "uv",
        "pip",
        "install",
        "--system",
    ]
    index_url = args.index_url or os.environ.get("PIP_INDEX_URL") or ""
    if index_url:
        command.extend(["--index-url", index_url])
    command.extend(dependencies)
    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
