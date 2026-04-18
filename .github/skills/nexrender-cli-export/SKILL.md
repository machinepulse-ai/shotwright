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
- Apply a JSX patch before export.
- Export a deterministic mp4 preview from After Effects.
- Review a render workflow cheaply with gpt-5.4-mini before spending a full model run.

## Procedure

1. Confirm the container already exposes the target AEP at a stable `file:///` path and that After Effects is mounted or auto-installed.
2. Start from [job-template.json](./assets/job-template.json) and replace the template source, composition, patch script path, and final output path.
3. Keep JSX limited to composition or layer edits. Do not trigger renders from JSX; let nexrender own the render queue and output handling.
4. Include a script asset only when you need to patch the project before render. The JSX should read parameters from Nexrender and avoid container-specific hardcoding outside the incoming file paths.
5. Run `nexrender-cli --job "<minified job json>"` inside the Windows container or through the same code path Shotwright uses in `src/backend/app/services/nexrender.py`.
6. Treat `result.mp4` or the copied output file as the success artifact even if nexrender exits non-zero. Shotwright already uses that recovery rule and you should keep the same behavior.
7. If you need a browser preview, follow up with the ffmpeg/HLS path already used by Shotwright after the mp4 exists.

## Repo-specific Rules

- Use `scripts/validate/validation_nexrender_job.json` as the canonical example of a job payload with a JSX patch asset.
- Use `scripts/validate/validation_patch.jsx` as the pattern for composition-level edits only.
- Preserve the current `@nexrender/action-encode` and `@nexrender/action-copy` split for mp4 outputs.
- Keep outputs under `C:\data\output` or the configured Shotwright export root so the rest of the stack can find them.

## References

- [render workflow reference](./references/render-workflow.md)
- [gpt-5.4-mini checklist](./references/gpt-5.4-mini-checklist.md)
- [job template](./assets/job-template.json)