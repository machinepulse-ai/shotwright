from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from skills_bundle import ensure_skills_bundle
from shotwright_config import get_default_config_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and extract the versioned Shotwright skills bundle into .github/skills.")
    parser.add_argument("--config", type=Path, default=get_default_config_path())
    parser.add_argument("--install-root", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--url-proxy-prefix", default=None)
    parser.add_argument("--download-concurrency", type=int, default=None)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--progress", dest="show_progress", action="store_true")
    parser.add_argument("--no-progress", dest="show_progress", action="store_false")
    parser.add_argument("--verify-ssl", dest="verify_ssl", action="store_true")
    parser.add_argument("--no-verify-ssl", dest="verify_ssl", action="store_false")
    parser.set_defaults(verify_ssl=True, show_progress=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    result = ensure_skills_bundle(
        source_repo_root=repo_root,
        install_root=args.install_root if args.install_root is not None else repo_root,
        config_path=args.config,
        force=args.force,
        proxy=args.proxy,
        url_proxy_prefix=args.url_proxy_prefix,
        download_concurrency=args.download_concurrency if args.download_concurrency is not None else None,
        show_progress=args.show_progress,
        verify_ssl=args.verify_ssl,
        github_token=os.environ.get(args.token_env) or None,
        log=print,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())