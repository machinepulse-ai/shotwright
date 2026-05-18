from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from download_utils import AdobeDownloadManager, DownloadTask, PackageStatus
from setup_versions import parse_setup_versions

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from shotwright_config import build_resolved_config, get_default_config_path, load_config


DEFAULT_SETUP = parse_setup_versions(Path(__file__).resolve().parents[2] / "setup-versions.yml")
_RESOLVED = build_resolved_config(load_config(get_default_config_path()))
DEFAULT_PAYLOAD_ROOT = Path(_RESOLVED.host.payload_root)
PROGRESS_BAR_WIDTH = 24
PROGRESS_REPORT_INTERVAL_SECONDS = 30.0


def format_bytes(value: int | float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            return f"{size:0.1f} {unit}" if unit != "B" else f"{size:0.0f} {unit}"
        size /= 1024.0
    return f"{size:0.1f} TiB"


def format_rate(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "0 B/s"
    return f"{format_bytes(bytes_per_second)}/s"


def progress_bar(progress: float) -> str:
    clamped = min(max(progress, 0.0), 1.0)
    filled = int(round(clamped * PROGRESS_BAR_WIDTH))
    return "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)


def active_package_summary(task: DownloadTask, *, limit: int = 3) -> str:
    active_packages = [
        package
        for dependency in task.dependencies_to_download
        for package in dependency.packages
        if package.status == PackageStatus.DOWNLOADING and not package.downloaded
    ]
    if not active_packages and task.current_package is not None:
        active_packages = [task.current_package]

    summaries: list[str] = []
    for package in active_packages[:limit]:
        summaries.append(f"{package.full_package_name} {package.progress * 100.0:0.1f}%")
    if len(active_packages) > limit:
        summaries.append(f"+{len(active_packages) - limit} more")
    return ", ".join(summaries) if summaries else "none"


async def report_download_progress(task: DownloadTask, stop_event: asyncio.Event) -> None:
    started_at = time.monotonic()
    last_downloaded = task.total_downloaded_size
    last_changed_at = started_at

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=PROGRESS_REPORT_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass

        now = time.monotonic()
        downloaded = task.total_downloaded_size
        if downloaded != last_downloaded:
            last_changed_at = now
            last_downloaded = downloaded

        elapsed = max(now - started_at, 1e-6)
        stalled_for = now - last_changed_at
        packages = [package for dependency in task.dependencies_to_download for package in dependency.packages]
        total_packages = len(packages)
        completed_packages = sum(1 for package in packages if package.downloaded)
        total_size = task.total_size or sum(package.download_size for package in packages)
        progress = task.total_progress if task.total_progress > 0 else (
            float(downloaded) / float(total_size) if total_size > 0 else 0.0
        )
        rate = task.total_speed if task.total_speed > 0 else downloaded / elapsed

        print(
            "[download] "
            f"{progress * 100.0:5.1f}% [{progress_bar(progress)}] "
            f"{format_bytes(downloaded)} / {format_bytes(total_size)} "
            f"at {format_rate(rate)}; "
            f"packages {completed_packages}/{total_packages}; "
            f"active: {active_package_summary(task)}; "
            f"no-byte-change {stalled_for:0.0f}s",
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download an After Effects payload layout for container installs.")
    parser.add_argument("--payload-root", type=Path, default=DEFAULT_PAYLOAD_ROOT)
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

    print(
        "[download] "
        f"resolving Adobe catalog for {args.product_id} {args.version} ({args.platform})...",
        flush=True,
    )
    plan = await manager.resolve_standard_download_plan(args.product_id, args.version)
    print(
        "[download] "
        f"resolved {plan.display_name}; secure CDN: {plan.secure_cdn}",
        flush=True,
    )

    task = DownloadTask(
        product_id=args.product_id,
        product_version=args.version,
        language=args.language,
        display_name=plan.display_name,
        directory=ae_dir,
        platform=args.platform,
    )

    manager.apply_download_plan(task, plan)
    packages = [package for dependency in task.dependencies_to_download for package in dependency.packages]
    print(
        "[download] "
        f"plan: {len(packages)} packages, {format_bytes(task.total_size)} total; "
        f"payload root: {payload_root}",
        flush=True,
    )

    print("[download] starting main After Effects payload download...", flush=True)
    stop_progress = asyncio.Event()
    progress_task = asyncio.create_task(report_download_progress(task, stop_progress))
    try:
        await manager.handle_custom_download(task, task.dependencies_to_download)
    finally:
        stop_progress.set()
        await progress_task

    print(
        "[download] "
        f"100.0% [{progress_bar(1.0)}] "
        f"{format_bytes(task.total_size)} / {format_bytes(task.total_size)}; "
        f"packages {task.total_packages}/{task.total_packages}; complete",
        flush=True,
    )

    if not args.skip_helper:
        print("[download] starting Creative Cloud helper payload download...", flush=True)

        async def report_helper_progress(progress: float, message: str) -> None:
            print(f"[helper] {progress:0.0%} {message}", flush=True)

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
