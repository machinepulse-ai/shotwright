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

1. Inspect the Shotwright workspace state first so you know whether a container, active project, or existing AEP already exists.
2. Ensure there is a running After Effects container before any JSX or render action.
3. If no suitable session project exists, create one through the managed project flow first. Use the project-creation tool so the new AEP is saved into a shared Shotwright workspace instead of an ad hoc container-only path.
4. For project creation JSX, save to `$.getenv("SHOTWRIGHT_PROJECT_FILE")` and call `app.quit()` when finished. For later edits against an existing project, keep saving back to that same path.
5. Keep JSX limited to project, composition, or layer edits. Do not trigger renders from JSX; let nexrender own the render queue and output handling.
6. Start from [job-template.json](./assets/job-template.json) and replace the template source, composition, patch script path, and final output path.
7. Include a script asset only when you need to patch the project before render. The JSX should read parameters from Nexrender and avoid container-specific hardcoding outside the incoming file paths.
8. Run nexrender through the same code path Shotwright uses in `src/backend/app/services/nexrender.py`, with an explicit `aerender.exe` path and a stable work directory.
9. Treat the copied mp4 output file as the success artifact even if nexrender exits non-zero. Shotwright already uses that recovery rule and you should keep the same behavior.
10. If the user asks for the editable project artifact too, finish by exporting the managed project archive after the render succeeds.

## Repo-specific Rules

- Use `scripts/validate/validation_nexrender_job.json` as the canonical example of a job payload with a JSX patch asset.
- Use `scripts/validate/validation_patch.jsx` as the pattern for composition-level edits only.
- For brand-new AEP creation, follow the structure in `scripts/validate/create_validation_animation_project.jsx`: create the project, save it to the managed workspace path, and quit After Effects.
- Preserve the current `@nexrender/action-encode` and `@nexrender/action-copy` split for mp4 outputs.
- Keep outputs under `C:\data\output` or the configured Shotwright export root so the rest of the stack can find them.

## References

- [render workflow reference](./references/render-workflow.md)
- [gpt-5.4-mini checklist](./references/gpt-5.4-mini-checklist.md)
- [job template](./assets/job-template.json)