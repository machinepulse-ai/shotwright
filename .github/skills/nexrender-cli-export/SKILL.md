---
name: nexrender-cli-export
description: 'Use when rendering After Effects projects with nexrender-cli, applying JSX patch scripts, exporting mp4 previews, or validating AE render jobs in Shotwright. Includes a reusable job template and a low-cost gpt-5.4-mini automation checklist.'
argument-hint: 'Describe the AEP path, composition, JSX patch intent, and desired mp4 output path.'
user-invocable: true
disable-model-invocation: false
---

# nexrender-cli Export

## When to Use

- Render an uploaded AEP through nexrender-cli.
- Create a brand-new AEP in a managed Shotwright project workspace before rendering.
- Apply a JSX patch before export.
- Export a deterministic mp4 preview from After Effects.
- Review a render workflow cheaply with gpt-5.4-mini before spending a full model run.

## Procedure

1. Inspect the Shotwright workspace state once at the start so you know the active project, container status, and any recent session image attachments.
2. Ensure there is a running After Effects container before any JSX or render action, and keep the default runtime image unless the user explicitly asks for another image.
3. If the user needs a blank AEP, prefer `create_empty_after_effects_project` instead of handwritten project-creation JSX.
4. If the user supplied a reference video, prefer `generate_storyboard_from_reference_video` first so the common visual reference is a single image in the shared temporary workspace.
5. If the user supplied inline images, prefer `stage_reference_images` or `create_reference_composition` so the assets land inside the shared project workspace. Do not copy them with shell commands.
6. Use `create_reference_composition` for the common setup path: import a staged image or generated storyboard, create or update a comp, and keep the project save path stable.
7. Use `create_after_effects_project` only when the user truly needs custom bootstrap JSX that the higher-level tools cannot cover.
8. Keep `run_after_effects_jsx` for later creative edits against an existing managed project. Do not use JSX to perform renders.
9. Run nexrender through the same code path Shotwright uses in `src/backend/app/services/nexrender.py`, with an explicit `aerender.exe` path and a stable work directory.
10. Treat the copied mp4 output file as the success artifact even if nexrender exits non-zero. Shotwright already uses that recovery rule and you should keep the same behavior.
11. If the user asks for the editable project artifact too, finish by exporting the managed project archive after the render succeeds.

## Normal Flow Guardrails

- Do not use `powershell`, `read_powershell`, `list_powershell`, `read_agent`, `task`, or subagents for the normal Shotwright render path when the built-in Shotwright tools can do the work.
- Do not open validation scripts, job templates, or reference markdown files during a routine render run unless the user asks for repo archaeology or a tool behavior is unclear.
- Do not override the container image, manually copy inline images, or hand-roll empty-project save boilerplate unless the user explicitly requests a nonstandard workflow.
- Only fall back to `powershell` exploration after every relevant higher-level Shotwright tool for the requested workflow has already failed.

## Repo-specific Rules

- Use `scripts/validate/validation_nexrender_job.json` as the canonical example of a job payload with a JSX patch asset.
- Use `scripts/validate/validation_patch.jsx` as the pattern for composition-level edits only.
- For brand-new AEP creation, follow the structure in `scripts/validate/create_validation_animation_project.jsx`: create the project, save it to the managed workspace path, and quit After Effects.
- Preserve the current `@nexrender/action-encode` and `@nexrender/action-copy` split for mp4 outputs.
- Keep outputs under `C:\data\output` or the configured Shotwright export root so the rest of the stack can find them.

## References

- These are fallback references, not a mandatory first step during a normal session run.
- [render workflow reference](./references/render-workflow.md)
- [gpt-5.4-mini checklist](./references/gpt-5.4-mini-checklist.md)
- [job template](./assets/job-template.json)