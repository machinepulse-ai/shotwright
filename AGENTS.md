# AGENTS

## Purpose

Shotwright is a container-first Adobe After Effects runtime for AI agents.

The project goal is not "AI video for everyone" in the shallow sense.
It is specifically about giving real AE designers cloud execution and agent leverage without forcing them to become infrastructure operators.

This repository should stay practical and designer-serving:
- keep the Windows container build reproducible
- keep validation renders easy to replay
- keep the person with taste in control while automation disappears into the background

## Current Working Model

- The Docker image provides Node.js, Python 3.13, ffmpeg, Git, and nexrender dependencies.
- Adobe After Effects is not baked into the image.
- Two runtime modes:
  1. **Host mount** — mount `C:\Program Files\Adobe\Adobe After Effects 2026` from the host into the container.
  2. **Payload install** — mount a licensed payload cache to `C:\lab\payload`; the container auto-installs AE at startup via `scripts/runtime_entrypoint.ps1`.
- `AUTO_INSTALL_AFTER_EFFECTS=1` is enabled by default in the Dockerfile. When no payload is mounted, the install step silently skips.
- Validation uses `scripts/validate/create_validation_animation_project.jsx` to generate an AEP and `scripts/validate/validation_patch.jsx` to patch text only.
- nexrender is responsible for the final render output. Do not mix custom render queue execution into the validation patch script.

## Directory Structure

Scripts are organized into two subdirectories:

```
scripts/
  install/           installer and payload-related scripts
  validate/          validation render scripts
  runtime_entrypoint.ps1   container entrypoint
  pull_mcr_image.py        MCR base image helper
```

When adding new scripts, place them in `install/` or `validate/` as appropriate. Keep `runtime_entrypoint.ps1` at the scripts root because the Dockerfile CMD references it directly.

## Brand Guardrails

- The project name is `Shotwright`.
- The story is creative empowerment, not generic automation.
- The target user is an AE designer who should be able to describe intent and let the system execute infrastructure on their behalf.
- Avoid framing the repo like a toy wrapper around After Effects. It should feel like a serious runtime foundation for future agent-driven creative systems.

## Important Context

- Proxy-aware builds are already part of the Dockerfile through `http_proxy` and related build args.
- `nvm.install` is optional in the Dockerfile because GitHub release downloads may fail behind some proxies.
- The validation job was intentionally changed to use `outputExt: mp4` and `@nexrender/action-copy` from `result.mp4` to the final output.
- A previous validation attempt mixed render control into the script asset and caused duplicate outputs plus confusing exit behavior. Keep the patch script render-free.
- MCP tools were removed in v0.2.0. Future automation should use VS Code skills, not MCP servers.
- `aerender -version` exits non-zero even when it reports a valid version. Check stdout content, not exit code.
- nexrender sometimes exits non-zero while still producing a valid `result.mp4`. The validation script recovers it from the work directory.

## Expected Validation Result

A clean validation run should leave:
- `validation-data/templates/validation_motion.aep`
- `validation-data/output/validation.mp4`

and should not require any extra `.done` marker for local smoke tests.

## Safe Change Boundaries

- If you change `Dockerfile`, re-check `.dockerignore` so the build context stays minimal.
- If you change validation scripts, rerun the manual validation flow before touching higher-level orchestration.
- If you move files under `scripts/`, update all container-side path references (the container sees these at `C:\workspace\scripts\...`).
- `scripts/validate/validation_nexrender_job.json` contains absolute container-side paths — update them if you rename or move JSX files.
- `scripts/runtime_entrypoint.ps1` and `scripts/install/install_after_effects_in_container.ps1` reference each other's container paths — keep them in sync.

## Validated Configuration

Last validated on **2026-04-17**:
- Image: `shotwright:latest` on Windows Server LTSC 2025 (process isolation)
- After Effects: version 26.2 (aerender 26.2x49)
- nexrender: `@nexrender/cli@1.63.3`, `@nexrender/action-copy@1.49.4`, `@nexrender/action-encode@1.46.8`
- Output: `validation.mp4` — 4 seconds, H.264, ~5 MB
- Both host-mount and payload-install modes verified

## Path Conventions

- **Documentation examples**: use `C:\data\...` as the canonical host-side base path.
- **Container workspace**: `C:\workspace` (repo mount point).
- **Container data**: `C:\data` (validation-data mount point).
- **Container payload**: `C:\lab\payload` (installer payload mount point).
- **Container AE install**: `C:\Program Files\Adobe\Adobe After Effects 2026`.

## CI Workflow

- `.github/workflows/windows-container-validation.yml`
- `dockerfile-build` job runs on every push/PR — verifies the image builds.
- `validation-render` job runs on manual `workflow_dispatch` only — requires `SHOTWRIGHT_INSTALLER_CACHE_URL` secret.
- Runner: `windows-2025`.

## Next Good Steps

- add integration tests around the validation command builders
- add remote worker pool support
- add job packaging for arbitrary AEP uploads
- add artifact retention and cleanup policies
- add a higher-level natural-language job model that maps designer intent to containerized execution
