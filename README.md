# Shotwright

Shotwright turns Adobe After Effects into a cloud-executable creative engine for AI agents.

Most "AI video" products win by shrinking the creative surface area: fewer choices, more templates, more lock-in. Shotwright takes the opposite position.

It is built for real motion designers, compositors, and AE-heavy teams who should not have to become DevOps engineers just to keep their edge. A designer should be able to describe the result they want, let an agent drive the runtime, and still keep After Effects at the center of the craft.

## The Story

Shotwright exists for a simple reason:

- ordinary AE designers should not need to wire Docker, Windows workers, JSX pipelines, or render queues by hand
- creative teams should not lose authority to shallow startup editing apps that trade taste for convenience
- the person with visual judgment should stay in control while infrastructure becomes invisible

The promise is straightforward:

Say what you want.
Let agents execute the boring parts.
Keep the creative leverage in After Effects.

## What Shotwright Is

Shotwright is a container runtime plus MCP control plane for Adobe After Effects.

It gives agents a reproducible way to:
- build a Windows After Effects runtime image
- mount a host Adobe After Effects installation into an isolated container
- generate or patch test AEP projects with JSX
- run nexrender-based validation renders in a repeatable way
- expose those actions through a deterministic MCP server

The project is inspired by Dakkshin/after-effects-mcp, but the emphasis here is different:
- after-effects-mcp focuses on direct After Effects control through MCP tools and a bridge panel
- Shotwright focuses on cloud-style Windows container infrastructure that agents can drive safely and repeatedly

## Who It Is For

- AE designers who want leverage without surrendering craft
- studios that need repeatable rendering infrastructure behind an agent workflow
- teams building AI-native creative tooling on top of real After Effects instead of toy editors
- technical operators who want a clean validation path before building remote worker fleets

## Why It Matters

Creative software is moving into a dangerous phase where tooling gets easier by making the output flatter.

Shotwright is built on a different bet:
- the future belongs to designers with taste plus infrastructure that disappears into the background
- agents should amplify high-skill creative workers, not route around them
- After Effects should become easier to command, not easier to replace

If this project succeeds, a designer does not need to fight infra to stay competitive. They can use natural language, reusable assets, and cloud execution to out-produce teams shipping template-first video apps.

## Current Scope

- Build a Windows container image with Node.js, Python 3.13, ffmpeg, Git, and nexrender dependencies.
- Mount `C:\Program Files\Adobe\Adobe After Effects 2026` from the host into the container.
- Generate a validation AEP with animated solids and text.
- Patch that AEP through nexrender without mixing custom render queue logic into the validation script.
- Produce a single validation mp4 as the expected smoke-test artifact.

## Project Layout

- `src/index.ts`: MCP server entrypoint.
- `src/config.ts`: runtime configuration loader.
- `src/shell.ts`: subprocess utilities for Docker and PowerShell execution.
- `src/validation.ts`: validation container orchestration and artifact checks.
- `scripts/create_validation_animation_project.jsx`: builds a mock animated AEP.
- `scripts/validation_patch.jsx`: patch-only JSX used by nexrender.
- `scripts/validation_nexrender_job.json`: minimal validation job.
- `scripts/run_validation.ps1`: manual smoke-test entrypoint.
- `Dockerfile`: Windows runtime image recipe.

## Requirements

- Windows host
- Docker with Windows containers enabled
- Adobe After Effects 2026 installed on the host
- Node.js 20+

## Quick Start

### 1. Build the image

```powershell
docker build -t shotwright:dev -f Dockerfile .
```

### 2. Install dependencies and build the MCP server

```powershell
npm install
npm run build
```

### 3. Run the MCP server

```powershell
npm start
```

### 4. Manual validation render

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_validation.ps1 -ImageTag shotwright:dev
```

Expected result:
- `validation-data/templates/validation_motion.aep`
- `validation-data/output/validation.mp4`

## MCP Tools

Current MCP tools exposed by the server:
- `shotwright_status`: report Docker/image/After Effects/asset readiness
- `shotwright_render_validation`: run the validation render end to end
- `shotwright_cleanup_validation`: remove the temporary validation container

## Design Notes

- The Docker image does not bundle Adobe After Effects itself.
- The runtime depends on a host mount of the Adobe install directory.
- Validation JSX is patch-only. nexrender owns the render output path.
- Proxy-aware Docker builds are supported through `http_proxy` and `https_proxy` build args.

## Roadmap

- add remote worker registration and leasing
- add job packaging for user-provided AEP assets
- add object storage upload/download hooks
- add container pool management for concurrent agents
- add richer MCP tools for composition packaging and artifact retrieval
- add a designer-first natural-language job layer on top of the runtime primitives

## License

MIT
