---
name: ffmpeg-storyboard
description: 'Use when generating storyboard contact sheets from exported mp4 files, sampling animation timing with ffmpeg, or comparing a Shotwright render against a reference clip. Includes a reusable PowerShell script and a gpt-5.4-mini review checklist.'
argument-hint: 'Describe the mp4 path, sample interval, column count, and what motion or timing you need to inspect.'
user-invocable: true
disable-model-invocation: false
---

# ffmpeg Storyboard

## When to Use

- Inspect a rendered mp4 without scrubbing frame-by-frame.
- Generate a contact sheet for visual QA.
- Compare a Shotwright output against a reference video.
- Run a cheap review pass with gpt-5.4-mini after the storyboard image exists.

## Procedure

1. Render or locate the target mp4.
2. Run [generate_storyboard.ps1](./scripts/generate_storyboard.ps1) with the input path, interval, width, and column count.
3. If you also have a reference clip, run the same script on the reference so both contact sheets use the same cadence.
4. Review the storyboard for motion continuity, title timing, transitions, and obvious missing layers.
5. If the storyboard looks suspicious, lower the interval and regenerate a denser sheet before re-rendering the whole project.

## Output Expectations

- The script emits a single jpg contact sheet next to the video unless you override the output path.
- It also prints a JSON summary with the chosen interval, grid size, and output file.

## References

- [storyboard review checklist](./references/review-checklist.md)
- [PowerShell generator](./scripts/generate_storyboard.ps1)