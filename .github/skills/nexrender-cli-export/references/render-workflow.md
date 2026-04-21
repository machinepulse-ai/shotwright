# Render Workflow Reference

Use this workflow when you need a predictable Windows-container render path.

1. Resolve the AEP location from the managed or uploaded Shotwright project workspace.
2. If the AEP does not exist yet, create it first through AfterFX.jsx and save it into the managed workspace path before trying to render.
3. Build a nexrender job JSON that points `template.src` at the AEP with a `file:///` URL.
4. If you need JSX edits, attach them as a `script` asset and pass parameters instead of hardcoding comp names or text values.
5. Encode to mp4 in postrender and copy the final artifact to the configured output path.
6. Treat the copied output artifact as the success signal even if nexrender exits non-zero.

Keep these repo constraints in mind:

- `scripts/validate/validation_nexrender_job.json` is the strongest existing example.
- `scripts/validate/validation_patch.jsx` should stay render-free.
- `src/backend/app/services/nexrender.py` is the runtime implementation to mirror when you need exact behavior.