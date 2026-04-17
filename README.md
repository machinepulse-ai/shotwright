<div align="center">

# Shotwright

### Container-first Adobe After Effects runtime for AI agents

Build Windows render workers, mount a real After Effects install, and validate nexrender output end to end without turning designers into infrastructure operators.

<p>
	<img src="https://img.shields.io/badge/windows%20containers-ltsc2025-0078D4?style=for-the-badge&logo=windows11&logoColor=white" alt="Windows Containers LTSC 2025" />
	<img src="https://img.shields.io/badge/after%20effects-2026-9999FF?style=for-the-badge&logo=adobeaftereffects&logoColor=white" alt="Adobe After Effects 2026" />
	<img src="https://img.shields.io/badge/node-%E2%89%A520-5FA04E?style=for-the-badge&logo=nodedotjs&logoColor=white" alt="Node 20 or newer" />
	<img src="https://img.shields.io/badge/nexrender-validated-0A7EA4?style=for-the-badge&logo=render&logoColor=white" alt="Validated with nexrender" />
	<img src="https://img.shields.io/badge/license-MIT-2EA44F?style=for-the-badge" alt="MIT License" />
</p>

<p>
	<a href="https://github.com/LiuChangFreeman/shotwright/stargazers">
		<img src="https://img.shields.io/github/stars/LiuChangFreeman/shotwright?style=social" alt="GitHub stars" />
	</a>
	<a href="https://github.com/LiuChangFreeman/shotwright/network/members">
		<img src="https://img.shields.io/github/forks/LiuChangFreeman/shotwright?style=social" alt="GitHub forks" />
	</a>
</p>

</div>

> [!IMPORTANT]
> Shotwright keeps After Effects at the center of the workflow. The goal is not generic AI video automation; it is reproducible AE runtime infrastructure that lets agents execute the boring parts while designers keep the taste and control.

<details>
<summary><strong>Jump to section</strong></summary>

- [Validation Demo](#-validation-demo)
- [Why Shotwright](#-why-shotwright)
- [Capabilities](#-capabilities)
- [Validation Flow](#-validation-flow)
- [Requirements](#-requirements)
- [Quick Start](#-quick-start)
- [MCP Tools](#-mcp-tools)
- [Project Layout](#-project-layout)
- [Design Notes](#-design-notes)
- [Roadmap](#-roadmap)

</details>

## ✨ Validation Demo

<p align="center">
	<a href="./validation-data/output/validation.mp4">
		<img src="./docs/assets/validation-preview.png" alt="Shotwright validation render preview" width="960" />
	</a>
</p>

<p align="center">
	<a href="./validation-data/output/validation.mp4">
		<img src="https://img.shields.io/badge/Open-validation.mp4-FF5A5F?style=for-the-badge&logo=adobeaftereffects&logoColor=white" alt="Open validation.mp4" />
	</a>
</p>

The current smoke test successfully renders a real mp4 through a Windows container, a mounted host After Effects installation, and nexrender.

| Artifact | Status | Notes |
| --- | --- | --- |
| `validation.mp4` | ✅ committed | Smoke-test render output for the current repo state |
| `validation_motion.aep` | 🟡 generated locally | Recreated during validation and intentionally kept out of Git to avoid unnecessary binary churn |

## 🎬 Why Shotwright

Most AI video products shrink the creative surface area: fewer decisions, fewer controls, more templates. Shotwright takes the opposite bet.

- Give AE designers agent leverage without asking them to become Windows container operators.
- Keep validation renders reproducible, replayable, and easy to audit.
- Make infrastructure disappear into the background while taste stays with the human.
- Treat After Effects like a serious runtime foundation, not a toy wrapper around a panel script.

The project is inspired by Dakkshin's after-effects-mcp, but the center of gravity here is different: worker runtime infrastructure first, deterministic tool execution second, and designer control throughout.

## 🧰 Capabilities

| Capability | What it means in practice |
| --- | --- |
| Windows runtime image | Builds a container with Node.js, Python 3.13, ffmpeg, Git, and nexrender dependencies |
| Host AE mount | Uses a real host installation of Adobe After Effects 2026 instead of baking AE into the image |
| Validation project generation | Creates a reproducible AEP from JSX so smoke tests are easy to replay |
| Patch-only validation script | Keeps the JSX focused on composition edits while nexrender owns rendering |
| MCP control plane | Exposes status, validation render, and cleanup operations through deterministic tools |

## 🔄 Validation Flow

```mermaid
flowchart LR
		H[Host Adobe After Effects 2026] -->|mounted into container| C[Shotwright Windows runtime]
		C --> P[create_validation_animation_project.jsx]
		P --> A[validation_motion.aep]
		A --> N[nexrender-cli]
		N --> S[validation_patch.jsx]
		S --> R[aerender.exe]
		R --> M[validation.mp4]
```

## 🧱 Requirements

- Windows host
- Docker with Windows containers enabled
- Adobe After Effects 2026 installed on the host
- Node.js 20+

> [!TIP]
> Proxy-aware builds are already wired through the Dockerfile via `http_proxy`, `https_proxy`, `HTTP_PROXY`, and `HTTPS_PROXY` build args.

## 🚀 Quick Start

### 1. Build the image

```powershell
docker build -t shotwright:dev -f Dockerfile .
```

<details>
<summary><strong>Proxy-friendly build example</strong></summary>

```powershell
$proxy = 'http://192.168.1.80:8080'
docker build `
	--build-arg http_proxy=$proxy `
	--build-arg https_proxy=$proxy `
	--build-arg HTTP_PROXY=$proxy `
	--build-arg HTTPS_PROXY=$proxy `
	-t shotwright:dev `
	-f Dockerfile .
```

</details>

### 2. Install dependencies and build the MCP server

```powershell
npm install
npm run build
```

### 3. Run the MCP server

```powershell
npm start
```

### 4. Run the validation render

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_validation.ps1 -ImageTag shotwright:dev
```

Expected result:

- `validation-data/templates/validation_motion.aep`
- `validation-data/output/validation.mp4`

## 🧪 MCP Tools

| Tool | Purpose |
| --- | --- |
| `shotwright_status` | Report Docker, image, After Effects, and artifact readiness |
| `shotwright_render_validation` | Run the validation render end to end |
| `shotwright_cleanup_validation` | Remove the temporary validation container |

## 📁 Project Layout

```text
src/
	config.ts                runtime configuration loader
	index.ts                 MCP server entrypoint
	shell.ts                 subprocess helpers for Docker and PowerShell
	validation.ts            validation orchestration and artifact checks

scripts/
	create_validation_animation_project.jsx   generates the mock animated AEP
	validation_patch.jsx                      patch-only JSX used by nexrender
	validation_nexrender_job.json             minimal nexrender job definition
	run_validation.ps1                        manual smoke-test entrypoint

validation-data/
	output/                  rendered validation artifacts
	templates/               generated validation AEP files
	work/                    nexrender working directories and logs
```

## 📝 Design Notes

- The Docker image does not bundle Adobe After Effects itself.
- The runtime expects the host path `C:\Program Files\Adobe\Adobe After Effects 2026` to be mounted into the container.
- Validation JSX is patch-only by design. nexrender owns output naming and render execution.
- The validation job intentionally uses `outputExt: mp4` and `@nexrender/action-copy` so the smoke test ends with a single predictable video artifact.

## 🗺️ Roadmap

- [ ] add integration tests around the command builders in `src/validation.ts`
- [ ] add remote worker pool support
- [ ] add job packaging for arbitrary AEP uploads
- [ ] add artifact retention and cleanup policies
- [ ] add a higher-level natural-language job model that maps designer intent to containerized execution

## 📄 License

MIT
