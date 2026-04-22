---
name: ffmpeg-storyboard
description: 'Use when generating storyboard contact sheets from uploaded reference videos or exported mp4 files, sampling animation timing with ffmpeg parameters, or comparing a Shotwright render against a reference clip. Prefer the built-in storyboard tool before any shell fallback.'
argument-hint: 'Describe the reference video path, sample interval, clip start or duration, column count, optional crop box, and what motion or timing you need to inspect.'
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

1. Inspect the Shotwright workspace once so you know whether the session already has uploaded `reference_videos` or existing `storyboards`.
2. Prefer `generate_storyboard_from_reference_video` for the normal path. Pass `reference_video_path`, `start_seconds`, `clip_duration_seconds`, `interval_seconds`, `columns`, `width`, and optionally `crop` instead of composing ffmpeg commands by hand.
3. Use `crop` when you need to inspect a local animation region rather than the whole frame. The preferred format is `x,y,width,height` in pixels or percentages such as `25%,10%,40%,35%`.
4. If you need to inspect the contact sheet visually, use the available file or image viewing tool on the returned `storyboard_image_path`.
5. If you also have a rendered Shotwright mp4 to compare, generate a storyboard for that clip with the same cadence and crop so the grids are comparable.
6. Review the storyboard for motion continuity, title timing, transitions, and obvious missing layers.
7. If the storyboard is too sparse, lower `interval_seconds` or shorten the sampled clip and regenerate a denser sheet.

## Normal Flow Guardrails

- Do not use `powershell`, `read_powershell`, `list_powershell`, `task`, or subagents for storyboard generation while `generate_storyboard_from_reference_video` can do the job.
- Use the PowerShell script in this skill only as a last-resort fallback after the higher-level Shotwright storyboard tool has already failed.
- When a storyboard is meant to guide After Effects reconstruction, prefer passing the generated `storyboard_image_path` into `create_reference_composition` rather than copying files manually.

## Output Expectations

- The normal Shotwright tool path emits a single jpg contact sheet into the session temporary uploads area and returns the chosen interval, clip range, grid size, and output path.
- The fallback PowerShell script emits a single jpg contact sheet next to the video unless you override the output path.

## References

- [storyboard review checklist](./references/review-checklist.md)
- [PowerShell generator](./scripts/generate_storyboard.ps1)