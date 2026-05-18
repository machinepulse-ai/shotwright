# AGENTS

## Purpose

Shotwright is a container-first Adobe After Effects runtime for AI agents.

The project is not trying to be a generic “AI video for everyone” product. It exists to give professional AE designers reliable cloud-style execution and automation leverage without forcing them to become infrastructure operators.

This repository should stay:

- reproducible in how it builds and runs Windows containers
- auditable in how validation renders are produced
- designer-first in how it hides infrastructure complexity behind clear tooling

## Current Working Model

- The Docker image includes Node.js, Python 3.13, ffmpeg, Git, and the runtime dependencies required by nexrender.
- The default worker image is `shotwright:allinone`.
- `shotwright:allinone` copies the published `ghcr.io/liuchangfreeman/shotwright/after-effects-setup:26.2` payload into the image and completes the After Effects install during image build.
- Validation still supports two explicit smoke-test paths when needed:
  1. **Host mount** — mount the host-side AE install resolved from `setup-versions.yml` into the container.
  2. **Installer-cache mode** — pull a pre-built installer payload image from GHCR first, or build the cache locally with `scripts/install/download_after_effects_payload.py`, then mount it at `C:\data\payload` and let the container install AE at startup through `scripts/runtime_entrypoint.ps1`.
- `AUTO_INSTALL_AFTER_EFFECTS` still controls the shared startup check, but the default runtime image already has AE installed so the installer path returns immediately.
- Validation uses `scripts/validate/create_validation_animation_project.jsx` to generate the test project and `scripts/validate/validation_patch.jsx` to make composition-level edits only.
- nexrender owns the final render execution and output handling. Do not move render-queue logic into the validation patch script.

## Directory Structure

Scripts are split into two functional areas:

```text
scripts/
  install/                 installer and cache-related scripts
  validate/                validation render scripts
  runtime_entrypoint.ps1   container entrypoint
  pull_container_image.py  OCI image helper for GHCR, MCR, and similar registries
```

New scripts should go under `install/` or `validate/` unless they are true top-level runtime entrypoints. Keep `runtime_entrypoint.ps1` at the root of `scripts/` because the Dockerfile references it directly.

## Brand Guardrails

- The product name is `Shotwright`.
- The story is creative empowerment, not generic automation.
- The target user is an AE designer who should be able to express intent while the system executes infrastructure concerns.
- Do not frame the repo as a thin wrapper around After Effects. Treat it as a serious runtime foundation for agent-driven creative workflows.

## Important Context

- Proxy-aware builds are already wired through the Dockerfile via `http_proxy`, `https_proxy`, `HTTP_PROXY`, and `HTTPS_PROXY`.
- `shotwright-config.json` is the shared source for host/runner/container paths, Docker base images, and tool version defaults. Prefer it over duplicating environment conventions in scripts or docs.
- `scripts/install/setup_versions.py` is the shared reader for `setup-versions.yml`. Prefer it over duplicating version parsing in docs or workflows.
- Local skills live only under `.github/skills`. Repository startup and development entrypoints should hydrate that directory from the versioned release bundle when it is missing.
- `nvm.install` is optional because GitHub release downloads can fail behind restrictive enterprise proxies.
- The validation flow deliberately uses `outputExt: mp4` and `@nexrender/action-copy` to copy `result.mp4` into the final output path.
- A previous validation design mixed render control into the patch script and caused duplicate outputs plus confusing failures. Keep the patch script render-free.
- MCP was removed in `v0.2.0`. Future automation should use VS Code skills, not MCP servers.
- `aerender -version` can return a non-zero exit code even when it prints a valid version. Check stdout, not the exit code alone.
- nexrender may also exit non-zero while still producing a valid `result.mp4`. The validation script already recovers that artifact from the work directory.

## Expected Validation Result

A clean validation run should produce:

- `validation-data/templates/validation_motion.aep`
- `validation-data/output/validation.mp4`

No `.done` marker files are required for the local smoke test.

## Safe Change Boundaries

- If you change the Dockerfile, re-check `.dockerignore` so the build context stays small.
- If you change validation behavior, rerun the manual validation flow before touching higher-level orchestration.
- If you move files under `scripts/`, update all container-side references accordingly.
- `scripts/validate/validation_nexrender_job.json` contains absolute container paths. Update it if JSX file names or locations change.
- `scripts/runtime_entrypoint.ps1` and `scripts/install/install_after_effects_in_container.ps1` must stay aligned on shared container paths.

## UI Testing Workflow

- For frontend layout or interaction regressions, use the documented Playwright flow in `tests/test.md` instead of creating new ad hoc `tmp-playwright-*.js` files.
- Reusable UI scripts live under `tests/ui/`.
- Screenshot artifacts belong under `tests/artifacts/`, not `validation-data/output/`.
- The session-page regression script is `tests/ui/session_page_regression.js`; use it before and after changing the right sidebar or session-level Copilot controls.

## Validated Configuration

Last validated on **2026-04-17**:

- Image: `shotwright:allinone` on Windows Server LTSC 2025 with process isolation
- After Effects: version 26.2 (`aerender 26.2x49`)
- nexrender: `@nexrender/cli@1.63.3`, `@nexrender/action-copy@1.49.4`, `@nexrender/action-encode@1.46.8`
- Output artifact: `validation.mp4`, 4 seconds, H.264, roughly 5 MB
- Both host-mount and installer-cache modes verified

## Path Conventions

- Documentation examples should use `C:\data\...` as the canonical host-side path.
- The repository mount inside the container is `C:\workspace`.
- Validation data inside the container is mounted at `C:\data`.
- Installer cache data is mounted at `C:\data\payload`.
- The AE install target should be resolved from `setup-versions.yml` through `scripts/install/setup_versions.py`, typically `C:\Program Files\Adobe\Adobe After Effects <year>`.
- If these path conventions need to change, update `shotwright-config.json` first so scripts, workflows, and docs can stay aligned.

## CI Workflow

- Workflow files: `.github/workflows/ae-setup-publish.yml` and `.github/workflows/windows-container-validation.yml`
- `ae-setup-publish` runs on push to `setup-versions.yml` and manual `workflow_dispatch`; downloads AE from Adobe, patches, and publishes to GHCR
- `dockerfile-build` runs on push and pull request events
- `validation-render` runs only on manual `workflow_dispatch`; pulls installer payload from GHCR
- Skills bundle publication is handled by the local scripts under `scripts/skills/`; repository initialization should hydrate `.github/skills` from the versioned release asset when the directory is missing.
- Runner target: `windows-2025`
- Setup image versions are tracked in `setup-versions.yml`

## Next Good Steps

- add integration tests around validation command builders and recovery logic
- add remote worker-pool support
- package arbitrary AEP uploads into reproducible jobs
- define artifact retention and cleanup policies
- build a higher-level job model that maps designer intent to containerized execution
