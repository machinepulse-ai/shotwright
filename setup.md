# After Effects Installer-Cache Setup In A Windows Container

This guide describes the validated installer-cache workflow for deploying After Effects 26.2 inside a Shotwright container.

Shotwright supports two runtime modes:

1. **Host mount** — mount an existing host-side Adobe After Effects 2026 install into the container.
2. **Installer-cache mode** — mount a licensed installer cache and let Shotwright install After Effects automatically at startup.

This document focuses on installer-cache mode.

> [!IMPORTANT]
> Shotwright does not publish or redistribute Adobe installers. Keep the installer cache in your own local storage or in a private artifact store.

## 1. Prepare the host cache directories

You need two host directories:

| Directory | Required contents |
| --- | --- |
| `C:\data\payload\AEFT_26.2_win64` | `driver.xml` and all AE package folders |
| `C:\data\payload\CreativeCloudHelper_win64` | `HDBox` and `IPC` directories |

If you need to build the installer cache from scratch, run:

```powershell
python scripts\install\download_after_effects_payload.py --payload-root C:\data\payload
```

That script relies on `scripts/install/download_utils.py` and the helper classes used to resolve Adobe packages and supporting downloads.

## 2. Prerequisites

1. Docker Desktop is running in Windows container mode. `docker info --format '{{.OSType}}'` should return `windows`.
2. You have already built, or are ready to build, a local Shotwright image.

## 3. Patch the Creative Cloud helper

Before first use, patch the helper installer with `scripts/install/modify_setup_win.py`:

```powershell
python scripts\install\modify_setup_win.py C:\data\payload\CreativeCloudHelper_win64\HDBox\Setup.exe
```

If the file has already been patched, the script reports `Setup.exe appears to be already patched.` and exits successfully.

## 4. Build the Shotwright image

```powershell
docker build -t shotwright:latest .
```

The Dockerfile defaults to `AUTO_INSTALL_AFTER_EFFECTS=1`.

## 5. Run the end-to-end install and validation flow

Use `scripts/validate/run_validation.ps1`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate\run_validation.ps1 `
    -ImageTag shotwright:latest `
    -AfterEffectsPayloadRoot 'C:\data\payload\AEFT_26.2_win64' `
    -CreativeCloudHelperRoot 'C:\data\payload\CreativeCloudHelper_win64'
```

This command does the following:

1. Starts a Windows container from the specified image.
2. Mounts the repository, validation data directory, AE installer cache, and Creative Cloud helper cache.
3. Lets `scripts/runtime_entrypoint.ps1` invoke `scripts/install/install_after_effects_in_container.ps1`.
4. Waits for `aerender.exe` to appear and report a version.
5. Generates `validation_motion.aep`.
6. Runs nexrender and ensures `validation.mp4` ends up in `validation-data/output`.

Expected result:

```text
validation-data\output\validation.mp4
```

## 6. Known issues

### Validation succeeds but nexrender exits non-zero

After Effects sometimes returns a non-zero exit code even when the render completed successfully. When that happens, `scripts/validate/run_validation.ps1` recovers the newest `result.mp4` from `validation-data/work`.

### `Invalid driverXML ... parameter specified`

This usually means the `driver.xml` path was split by spaces. Use space-free paths such as `C:\data\payload\AEFT_26.2_win64`.

### `Path:C:\adobeTemp already exists`

This message is non-fatal. The installer is reusing a temporary extraction directory and can continue.

## 7. Acceptance criteria

1. The install completes without `Adobe Setup is not Authorized`.
2. `C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\aerender.exe` exists inside the container.
3. `aerender -version` prints a valid version string.
4. `validation.mp4` is produced under `validation-data/output`.
