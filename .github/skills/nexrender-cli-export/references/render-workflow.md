# Render Workflow Reference

Use this workflow when you need a predictable Windows-container render path.

1. Resolve the AEP location from the uploaded project workspace.
2. Build a nexrender job JSON that points `template.src` at the AEP with a `file:///` URL.
3. If you need JSX edits, attach them as a `script` asset and pass parameters instead of hardcoding comp names or text values.
4. Encode to mp4 in postrender and copy the final artifact to the configured output path.
5. Check stdout for `result.mp4` even if the exit code is non-zero.

Keep these repo constraints in mind:

- `scripts/validate/validation_nexrender_job.json` is the strongest existing example.
- `scripts/validate/validation_patch.jsx` should stay render-free.
- `src/backend/app/services/nexrender.py` is the runtime implementation to mirror when you need exact behavior.