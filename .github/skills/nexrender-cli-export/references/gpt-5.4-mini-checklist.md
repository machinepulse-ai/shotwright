# gpt-5.4-mini Checklist

Use `gpt-5.4-mini` when you only need an automation review pass instead of a full creative reasoning run.

Ask it to verify:

1. The job JSON points at a valid `file:///` AEP path.
2. The JSX asset only patches project state and does not try to render directly.
3. The postrender actions still encode and copy an mp4.
4. The final output path stays under `C:\data\output` or the Shotwright export root.
5. Recovery logic still checks for a produced mp4 even on non-zero nexrender exits.