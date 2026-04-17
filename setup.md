# After Effects Payload Install In A Windows Container

This document describes the validated payload-install workflow for After Effects 26.2 inside a Shotwright container.

The runtime supports two modes:

1. **Host mount** — Mount a host-side After Effects 2026 installation into the container.
2. **Payload install** — Mount a licensed payload cache and let Shotwright install After Effects automatically at startup.

This guide focuses on the payload-install mode.

> [!IMPORTANT]
> Shotwright does not publish or redistribute Adobe installer payloads. Keep them in your own local cache or a private artifact store.

## 1. Prepare the host payload directories

You need two directories:

| Directory | Required contents |
| --- | --- |
| `C:\data\payload\AEFT_26.2_win64` | `driver.xml` and all AE package folders |
| `C:\data\payload\CreativeCloudHelper_win64` | `HDBox` and `IPC` directories |

If you need to build a fresh payload cache, use:

```powershell
python scripts\install\download_after_effects_payload.py --payload-root C:\data\payload
```

That script uses [scripts/install/download_utils.py](scripts/install/download_utils.py) and the classes `AdobeDownloadManager`, `DownloadTask`, and `download_creative_cloud_helper_packages`.

## 2. Prerequisites

1. Docker Desktop running in Windows container mode (`docker info --format '{{.OSType}}'` returns `windows`).
2. A Shotwright image built locally, or readiness to build one.

## 3. Patch the helper installer (one-time)

Use [scripts/install/modify_setup_win.py](scripts/install/modify_setup_win.py):

```powershell
python scripts\install\modify_setup_win.py C:\data\payload\CreativeCloudHelper_win64\HDBox\Setup.exe
```

If the file was already patched, the script reports `Setup.exe appears to be already patched.`

## 4. Build the Shotwright image

```powershell
docker build -t shotwright:latest .
```

The Dockerfile defaults to `AUTO_INSTALL_AFTER_EFFECTS=1`.

## 5. Run the end-to-end install and validation render

Use [scripts/validate/run_validation.ps1](scripts/validate/run_validation.ps1):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate\run_validation.ps1 `
    -ImageTag shotwright:latest `
    -AfterEffectsPayloadRoot 'C:\data\payload\AEFT_26.2_win64' `
    -CreativeCloudHelperRoot 'C:\data\payload\CreativeCloudHelper_win64'
```

What this does:

1. Starts a Windows container from the specified image.
2. Mounts the repository, validation data, AE payload, and helper payload directories.
3. Lets `scripts/runtime_entrypoint.ps1` call [scripts/install/install_after_effects_in_container.ps1](scripts/install/install_after_effects_in_container.ps1).
4. Waits for `aerender.exe` to appear and report a version.
5. Generates `validation_motion.aep`.
6. Runs nexrender and ensures `validation.mp4` lands in `validation-data/output`.

Expected result:

```text
validation-data\output\validation.mp4
```

## 6. Known issues

### Validation output exists but nexrender exits non-zero

AE sometimes returns a non-zero exit code even when the render completes successfully. [scripts/validate/run_validation.ps1](scripts/validate/run_validation.ps1) recovers the freshest `result.mp4` from `validation-data/work` when this happens.

### `Invalid driverXML ... parameter specified`

The `driver.xml` path was split by spaces. Use space-free paths like `C:\data\payload\AEFT_26.2_win64`.

### `Path:C:\adobeTemp already exists`

Non-fatal. The installer reuses temporary extraction directories. Safe to ignore.

## 7. Acceptance criteria

1. The installer finishes without `Adobe Setup is not Authorized`.
2. `C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\aerender.exe` exists inside the container.
3. `aerender -version` returns a valid version string.
4. `validation.mp4` is produced under `validation-data/output`.