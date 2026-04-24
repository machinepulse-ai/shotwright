from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Any


# ---------------------------------------------------------------------------
# Raw config dataclasses – mirror the structure of shotwright-config.json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostWindowsPaths:
    data_root: str
    payload_dir_name: str
    image_archive_dir_name: str


@dataclass(frozen=True)
class GitHubRunnerPaths:
    payload_temp_dir_name: str
    setup_build_context_dir_name: str


@dataclass(frozen=True)
class ContainerPaths:
    repository_root: str
    data_root: str
    payload_dir_name: str
    templates_dir_name: str
    output_dir_name: str
    work_dir_name: str
    desktop_common_root: str
    adobe_install_base_root: str


@dataclass(frozen=True)
class PathsConfig:
    host_windows: HostWindowsPaths
    github_runner: GitHubRunnerPaths
    container: ContainerPaths


@dataclass(frozen=True)
class WorkspaceLayout:
    validation_data_dir_name: str
    templates_dir_name: str
    output_dir_name: str
    work_dir_name: str
    validation_project_file_name: str
    validation_output_file_name: str


@dataclass(frozen=True)
class DockerDefaults:
    runtime_base_image: str
    setup_base_image: str
    default_ci_image_tag: str


@dataclass(frozen=True)
class CIDefaults:
    python_version: str


@dataclass(frozen=True)
class SkillsBundleDefaults:
    artifact_version: str
    release_repo: str


@dataclass(frozen=True)
class ToolingConfig:
    docker: DockerDefaults
    ci: CIDefaults
    skills: SkillsBundleDefaults


@dataclass(frozen=True)
class ShotwrightConfig:
    paths: PathsConfig
    workspace: WorkspaceLayout
    tooling: ToolingConfig


# ---------------------------------------------------------------------------
# Derived (resolved) config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedHostPaths:
    payload_root: str
    image_archive_root: str


@dataclass(frozen=True)
class DerivedRunnerPaths:
    payload_root: str
    setup_build_context_root: str


@dataclass(frozen=True)
class DerivedContainerPaths:
    payload_root: str
    templates_root: str
    output_root: str
    work_root: str


@dataclass(frozen=True)
class DerivedWorkspacePaths:
    project_root: str
    validation_data_root: str
    templates_root: str
    output_root: str
    work_root: str
    validation_project_path: str
    validation_output_path: str


@dataclass(frozen=True)
class DerivedSkillsBundle:
    artifact_version: str
    artifact_file_name: str
    checksum_file_name: str
    release_tag: str
    release_repo: str
    artifact_download_url: str
    checksum_download_url: str


@dataclass(frozen=True)
class ResolvedConfig:
    raw: ShotwrightConfig
    host: DerivedHostPaths
    runner: DerivedRunnerPaths
    container: DerivedContainerPaths
    workspace: DerivedWorkspacePaths
    skills: DerivedSkillsBundle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


SKILLS_ARTIFACT_BASENAME = "shotwright-skills"
SKILLS_RELEASE_TAG_PREFIX = "skills-v"
GITHUB_RELEASE_DOWNLOAD_BASE_URL = "https://github.com"


def get_default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "shotwright-config.json"


def _join_windows(root: str, *parts: str) -> str:
    path = PureWindowsPath(root)
    for part in parts:
        if not part:
            continue
        path = path / part
    return str(path)


def _resolve_field(data: dict[str, Any], field: str) -> Any:
    current: Any = data
    for part in field.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        raise KeyError(field)
    return current


def build_skills_artifact_file_name(version: str) -> str:
    return f"{SKILLS_ARTIFACT_BASENAME}-{version}.zip"


def build_skills_release_tag(version: str) -> str:
    return f"{SKILLS_RELEASE_TAG_PREFIX}{version}"


def build_github_release_asset_url(repo: str, tag: str, file_name: str) -> str:
    return f"{GITHUB_RELEASE_DOWNLOAD_BASE_URL}/{repo}/releases/download/{tag}/{file_name}"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> ShotwrightConfig:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    p = raw["paths"]
    skills = raw.get("tooling", {}).get("skills", {})
    return ShotwrightConfig(
        paths=PathsConfig(
            host_windows=HostWindowsPaths(
                data_root=p["hostWindows"]["dataRoot"],
                payload_dir_name=p["hostWindows"]["payloadDirName"],
                image_archive_dir_name=p["hostWindows"]["imageArchiveDirName"],
            ),
            github_runner=GitHubRunnerPaths(
                payload_temp_dir_name=p["githubRunnerWindows"]["payloadTempDirName"],
                setup_build_context_dir_name=p["githubRunnerWindows"]["setupBuildContextDirName"],
            ),
            container=ContainerPaths(
                repository_root=p["windowsContainer"]["repositoryRoot"],
                data_root=p["windowsContainer"]["dataRoot"],
                payload_dir_name=p["windowsContainer"]["payloadDirName"],
                templates_dir_name=p["windowsContainer"]["templatesDirName"],
                output_dir_name=p["windowsContainer"]["outputDirName"],
                work_dir_name=p["windowsContainer"]["workDirName"],
                desktop_common_root=p["windowsContainer"]["desktopCommonRoot"],
                adobe_install_base_root=p["windowsContainer"]["adobeInstallBaseRoot"],
            ),
        ),
        workspace=WorkspaceLayout(
            validation_data_dir_name=raw["workspace"]["validationDataDirName"],
            templates_dir_name=raw["workspace"]["templatesDirName"],
            output_dir_name=raw["workspace"]["outputDirName"],
            work_dir_name=raw["workspace"]["workDirName"],
            validation_project_file_name=raw["workspace"]["validationProjectFileName"],
            validation_output_file_name=raw["workspace"]["validationOutputFileName"],
        ),
        tooling=ToolingConfig(
            docker=DockerDefaults(
                runtime_base_image=raw["tooling"]["docker"]["runtimeBaseImage"],
                setup_base_image=raw["tooling"]["docker"]["setupBaseImage"],
                default_ci_image_tag=raw["tooling"]["docker"]["defaultCiImageTag"],
            ),
            ci=CIDefaults(
                python_version=raw["tooling"]["ci"]["pythonVersion"],
            ),
            skills=SkillsBundleDefaults(
                artifact_version=skills.get("artifactVersion", "0.0.1"),
                release_repo=skills.get("releaseRepo", "LiuChangFreeman/shotwright"),
            ),
        ),
    )


def build_resolved_config(
    config: ShotwrightConfig,
    *,
    workspace_root: str | None = None,
    runner_temp: str | None = None,
) -> ResolvedConfig:
    host = config.paths.host_windows
    runner = config.paths.github_runner
    ctr = config.paths.container
    ws = config.workspace
    skills = config.tooling.skills
    artifact_file_name = build_skills_artifact_file_name(skills.artifact_version)
    checksum_file_name = f"{artifact_file_name}.sha256"
    release_tag = build_skills_release_tag(skills.artifact_version)

    project_root = str(Path(workspace_root).resolve()) if workspace_root else ""
    vdata_root = str(Path(workspace_root).resolve() / ws.validation_data_dir_name) if workspace_root else ""
    templates_root = str(Path(vdata_root) / ws.templates_dir_name) if vdata_root else ""
    output_root = str(Path(vdata_root) / ws.output_dir_name) if vdata_root else ""
    work_root = str(Path(vdata_root) / ws.work_dir_name) if vdata_root else ""

    return ResolvedConfig(
        raw=config,
        host=DerivedHostPaths(
            payload_root=_join_windows(host.data_root, host.payload_dir_name),
            image_archive_root=_join_windows(host.data_root, host.image_archive_dir_name),
        ),
        runner=DerivedRunnerPaths(
            payload_root=_join_windows(runner_temp, runner.payload_temp_dir_name) if runner_temp else "",
            setup_build_context_root=_join_windows(runner_temp, runner.setup_build_context_dir_name) if runner_temp else "",
        ),
        container=DerivedContainerPaths(
            payload_root=_join_windows(ctr.data_root, ctr.payload_dir_name),
            templates_root=_join_windows(ctr.data_root, ctr.templates_dir_name),
            output_root=_join_windows(ctr.data_root, ctr.output_dir_name),
            work_root=_join_windows(ctr.data_root, ctr.work_dir_name),
        ),
        workspace=DerivedWorkspacePaths(
            project_root=project_root,
            validation_data_root=vdata_root,
            templates_root=templates_root,
            output_root=output_root,
            work_root=work_root,
            validation_project_path=str(Path(templates_root) / ws.validation_project_file_name) if templates_root else "",
            validation_output_path=str(Path(output_root) / ws.validation_output_file_name) if output_root else "",
        ),
        skills=DerivedSkillsBundle(
            artifact_version=skills.artifact_version,
            artifact_file_name=artifact_file_name,
            checksum_file_name=checksum_file_name,
            release_tag=release_tag,
            release_repo=skills.release_repo,
            artifact_download_url=build_github_release_asset_url(skills.release_repo, release_tag, artifact_file_name),
            checksum_download_url=build_github_release_asset_url(skills.release_repo, release_tag, checksum_file_name),
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load and expand Shotwright shared configuration.")
    parser.add_argument("--config", type=Path, default=get_default_config_path())
    parser.add_argument("--field", default=None)
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--runner-temp", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config.resolve())
    resolved = build_resolved_config(config, workspace_root=args.workspace_root, runner_temp=args.runner_temp)
    data = asdict(resolved)

    if args.field:
        value = _resolve_field(data, args.field)
        if isinstance(value, (dict, list)):
            print(json.dumps(value, indent=2))
        else:
            print(value)
    else:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
