# AGENTS

## Purpose

Shotwright is a container-first Adobe After Effects runtime for AI agents.

The project goal is not "AI video for everyone" in the shallow sense.
It is specifically about giving real AE designers cloud execution and agent leverage without forcing them to become infrastructure operators.

This repository should stay practical and designer-serving:
- keep the Windows container build reproducible
- keep validation renders easy to replay
- keep MCP tools thin and deterministic
- keep the person with taste in control while automation disappears into the background

## Current Working Model

- The Docker image provides Node.js, Python 3.13, ffmpeg, Git, and nexrender dependencies.
- Adobe After Effects is not baked into the image.
- The runtime must mount the host path `C:\Program Files\Adobe\Adobe After Effects 2026` into the container.
- Validation uses `scripts/create_validation_animation_project.jsx` to generate an AEP and `scripts/validation_patch.jsx` to patch text only.
- nexrender is responsible for the final render output. Do not mix custom render queue execution into the validation patch script.

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

## Expected Validation Result

A clean validation run should leave:
- `validation-data/templates/validation_motion.aep`
- `validation-data/output/validation.mp4`

and should not require any extra `.done` marker for local smoke tests.

## Safe Change Boundaries

- If you change `Dockerfile`, re-check `.dockerignore` so the build context stays minimal.
- If you change validation scripts, rerun the manual validation flow before touching MCP orchestration.
- If you change the MCP server, keep tool outputs structured and concise because agents will parse them.

## Next Good Steps

- add integration tests around the command builders in `src/validation.ts`
- add remote worker pool support
- add job packaging for arbitrary AEP uploads
- add artifact retention and cleanup policies
- add a higher-level natural-language job model that maps designer intent to containerized execution
