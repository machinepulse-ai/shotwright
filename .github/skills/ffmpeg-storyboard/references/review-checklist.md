# Storyboard Review Checklist

Use `gpt-5.4-mini` when you want a low-cost QA pass over storyboard outputs.

Have it check:

1. Whether the major beats appear at the expected cadence.
2. Whether title cards, transitions, or overlays disappear unexpectedly.
3. Whether the reference clip and the Shotwright render diverge in framing or timing.
4. Whether another render is required, or the issue can be isolated to a specific comp or JSX patch.

When the storyboard still looks ambiguous, regenerate it with a smaller interval before requesting another full AE render.