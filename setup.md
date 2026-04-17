# After Effects Payload Install In A Windows Container

This document describes the validated payload-install workflow for After Effects 26.2 inside `shotwright:latest`.

The current repository supports two runtime modes:

1. Mount a host-side After Effects 2026 installation into the container.
2. Mount a licensed payload cache into the container and let Shotwright install After Effects automatically at startup.

This guide focuses on the second mode because it is the path validated on 2026-04-17 with the local payload directories below.

> [!IMPORTANT]
> Shotwright does not publish or redistribute Adobe installer payloads. Keep them in your own local cache or a private artifact store.

## 1. Prepare the host payload directories

The validated local test used these existing directories:

```text
D:\Downloads\Adobe Downloader AEFT_26.2-ALL-win64
D:\Downloads\AdobeDesktopCommon-win64
```

The first directory must contain `driver.xml` and the AE package folders. The second directory must contain `HDBox` and `IPC`.

If you need to build a fresh payload cache instead, use:

```powershell
& .\.venv\Scripts\python.exe .\scripts\download_after_effects_payload.py --payload-root C:\ae-container-lab\payload
```

That script uses [scripts/download_utils.py](scripts/download_utils.py) and the cleaned names `AdobeDownloadManager`, `DownloadTask`, and `download_creative_cloud_helper_packages`.

## 2. Prerequisites

Before you start, make sure:

1. Docker Desktop is running in Windows container mode.
2. `docker info --format '{{.OSType}}'` returns `windows`.
3. The image `shotwright:latest` exists locally, or you are ready to build it.
4. The repository Python environment is ready.

## 3. Patch the helper installer once on the host

Use [scripts/modify_setup_win.py](scripts/modify_setup_win.py):

```powershell
& .\.venv\Scripts\python.exe .\scripts\modify_setup_win.py "D:\Downloads\AdobeDesktopCommon-win64\HDBox\Setup.exe"
```

For the validated local payload, the file was already patched and the script reported `Setup.exe appears to be already patched.`

## 4. Build the Shotwright image

The Dockerfile now defaults to automatic container-side installation with `AUTO_INSTALL_AFTER_EFFECTS=1`.

```powershell
docker build -t shotwright:latest .
```

Keep the legacy typo tag only if you need compatibility with older notes:

```powershell
docker tag shotwright:latest shortwright:latest
```

## 5. Run the end-to-end install and validation render

Use [scripts/run_validation.ps1](scripts/run_validation.ps1):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_validation.ps1 `
    -ImageTag shotwright:latest `
    -AfterEffectsPayloadRoot "D:\Downloads\Adobe Downloader AEFT_26.2-ALL-win64" `
    -CreativeCloudHelperRoot "D:\Downloads\AdobeDesktopCommon-win64"
```

What this does:

1. Starts a Windows container from `shotwright:latest`.
2. Mounts the repository, validation data directory, AE payload directory, and helper payload directory.
3. Lets `scripts/runtime_entrypoint.ps1` call [scripts/install_after_effects_in_container.ps1](scripts/install_after_effects_in_container.ps1).
4. Waits for `aerender.exe` to appear and report a version.
5. Generates `validation_motion.aep`.
6. Runs `nexrender` and ensures `validation.mp4` lands in `validation-data/output`.

Validated local result:

```text
validation-data\output\validation.mp4
```

`ffprobe` reported:

```text
format_name=mov,mp4,m4a,3gp,3g2,mj2
duration=4.000000
```

## 6. Known issues

### Validation output exists but `nexrender` exits non-zero

Cause:

1. Some AE builds still leave a valid `result.mp4` even when `nexrender` treats the `aerender` process as failed.

Fix:

1. [scripts/run_validation.ps1](scripts/run_validation.ps1) now copies the freshest `result.mp4` from `validation-data/work` into `validation-data/output/validation.mp4` when needed.

### `Invalid driverXML ... parameter specified`

Cause:

1. The `driver.xml` path was split by spaces.

Fix:

1. Use a space-free host root such as `C:\ae-container-lab`.
2. Use a space-free payload directory such as `AEFT_26.2_win64`.
3. Pass `--driverXML=C:\lab\payload\AEFT_26.2_win64\driver.xml` exactly.

### `Path:C:\adobeTemp already exists`

Cause:

1. The installer creates and reuses temporary extraction directories while processing packages in parallel.

Fix:

1. In the validated run, this was non-fatal and did not block installation.
2. If you want cleaner logs before a retry, remove `C:\adobeTemp` inside the container and start the install again.

## 7. Acceptance criteria

You can treat the workflow as successful when all of the following are true:

1. The installer finishes without `Adobe Setup is not Authorized`.
2. `C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\aerender.exe` exists inside the container.
3. `aerender -version` returns a valid version string.
4. If you run the optional smoke test, `validation.mp4` is produced under the mounted lab root.

This workflow was validated with:

1. `shotwright:latest`
2. After Effects `26.2`
3. Windows target platform `win64`