from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from download_utils import AdobeDownloadManager, DownloadTask
from setup_versions import parse_setup_versions


DEFAULT_SETUP = parse_setup_versions(Path(__file__).resolve().parents[2] / "setup-versions.yml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download an After Effects payload layout for container installs.")
    parser.add_argument("--payload-root", type=Path, default=Path(r"C:\ae-container-lab\payload"))
    parser.add_argument("--product-id", default=DEFAULT_SETUP["product_id"])
    parser.add_argument("--version", default=DEFAULT_SETUP["current"])
    parser.add_argument("--language", default="ALL")
    parser.add_argument("--platform", default=DEFAULT_SETUP["platform"])
    parser.add_argument("--proxy-url", default=None)
    parser.add_argument("--skip-helper", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    payload_root = args.payload_root.resolve()
    ae_dir = payload_root / f"{args.product_id}_{args.version}_{args.platform}"
    helper_dir = payload_root / f"CreativeCloudHelper_{args.platform}"

    manager = AdobeDownloadManager(
        target_platform=args.platform,
        proxy_url=args.proxy_url,
        cc_target_directory=helper_dir,
    )

    plan = await manager.resolve_standard_download_plan(args.product_id, args.version)
    task = DownloadTask(
        product_id=args.product_id,
        product_version=args.version,
        language=args.language,
        display_name=plan.display_name,
        directory=ae_dir,
        platform=args.platform,
    )

    manager.apply_download_plan(task, plan)
    await manager.handle_custom_download(task, task.dependencies_to_download)

    if not args.skip_helper:
        async def report_helper_progress(progress: float, message: str) -> None:
            print(f"[{progress:0.0%}] {message}")

        await manager.download_creative_cloud_helper_packages(
            progress_handler=report_helper_progress,
            cancellation_handler=lambda: False,
            should_process=False,
            target_directory=helper_dir,
        )

    print(f"After Effects payload: {ae_dir}")
    print(f"Creative Cloud helper payload: {helper_dir}")


if __name__ == "__main__":
    asyncio.run(main())