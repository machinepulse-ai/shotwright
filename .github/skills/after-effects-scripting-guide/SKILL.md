---
name: after-effects-scripting-guide
description: 'Use when writing After Effects JSX or ExtendScript to create or edit .aep projects, create compositions, folders, solids, text or shape layers, import footage, configure the Render Queue or Output Module, queue jobs in Adobe Media Encoder, or export video. Uses the bundled local After Effects scripting guide snapshot under ./docs and includes Shotwright-specific guidance for MP4 and H.264 handoff.'
metadata:
  argument-hint: 'Describe the AEP save path, composition settings, layers or footage to create, and the desired render output format.'
  user-invocable: true
  disable-model-invocation: false
---

# After Effects Scripting Guide

## When to Use

- Write JSX or ExtendScript that creates or edits an After Effects project.
- Create compositions, folders, solids, text, shape layers, or imported footage items.
- Configure the Render Queue, Output Module, or AME handoff.
- Decide whether a requested export can stay inside AE scripting or must hand off to nexrender, aerender, or AME.

## How to Use This Skill

1. Treat [docs/index.md](./docs/index.md) as the bundled source-of-truth snapshot.
2. For application and project lifecycle, read [docs/general/application.md](./docs/general/application.md) and [docs/general/project.md](./docs/general/project.md).
3. For creating folders, items, and comps, read [docs/item/itemcollection.md](./docs/item/itemcollection.md) and [docs/item/compitem.md](./docs/item/compitem.md).
4. For layer creation, read [docs/layer/layercollection.md](./docs/layer/layercollection.md), [docs/layer/textlayer.md](./docs/layer/textlayer.md), and [docs/sources/solidsource.md](./docs/sources/solidsource.md).
5. For footage import, read [docs/other/importoptions.md](./docs/other/importoptions.md).
6. For render queue control, read [docs/renderqueue/renderqueue.md](./docs/renderqueue/renderqueue.md), [docs/renderqueue/renderqueueitem.md](./docs/renderqueue/renderqueueitem.md), [docs/renderqueue/rqitemcollection.md](./docs/renderqueue/rqitemcollection.md), and [docs/renderqueue/outputmodule.md](./docs/renderqueue/outputmodule.md).
7. If you need lower-level property access, match names, or text APIs, load the exact file you need from [docs/property](./docs/property/property.md), [docs/matchnames](./docs/matchnames/layer/avlayer.md), or [docs/text](./docs/text/textdocument.md).
8. If you need broader navigation first, start at [docs/introduction/overview.md](./docs/introduction/overview.md) or [docs/introduction/objectmodel.md](./docs/introduction/objectmodel.md).

## Working Rules

- In unattended runs, avoid save or open dialogs by passing explicit `File` objects and deliberately closing existing projects first.
- Create compositions with documented ranges only: width and height in pixels, duration in seconds, frame rate in FPS, and `bgColor` as normalized `[R, G, B]` floats in `[0.0..1.0]`.
- Import footage through `ImportOptions`; do not invent undocumented import shortcuts.
- Before calling `renderQueue.render()`, always set `outputModule(1).file` and only apply a named template after confirming it exists locally.
- Use the bundled docs as the reference layer. Do not add parallel hand-written summaries unless they encode Shotwright-specific policy that is missing from the upstream guide.

## Shotwright Policy

- The upstream guide documents project creation, item creation, layer creation, Render Queue setup, Output Module access, and AME handoff.
- The upstream guide does not provide a robust, portable JSX API for directly controlling final MP4 or H.264 codec settings across environments.
- In Shotwright, keep JSX responsible for project structure and render-queue configuration.
- If the deliverable must be MP4, prefer the Shotwright render pipeline, nexrender, aerender, or explicit AME handoff for final encoding.

## Minimal Paths

- New or save project: [docs/general/project.md](./docs/general/project.md)
- Add comp or folder: [docs/item/itemcollection.md](./docs/item/itemcollection.md)
- Configure comp: [docs/item/compitem.md](./docs/item/compitem.md)
- Add solid or text: [docs/layer/layercollection.md](./docs/layer/layercollection.md)
- Text specifics: [docs/layer/textlayer.md](./docs/layer/textlayer.md)
- Import footage: [docs/other/importoptions.md](./docs/other/importoptions.md)
- Queue and render: [docs/renderqueue/renderqueue.md](./docs/renderqueue/renderqueue.md)
- Output file and templates: [docs/renderqueue/outputmodule.md](./docs/renderqueue/outputmodule.md)

