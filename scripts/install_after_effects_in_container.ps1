param(
    [string]$InstallerPayloadRoot = 'C:\lab\payload',
    [string]$AfterEffectsPayloadRoot = '',
    [string]$CreativeCloudHelperRoot = '',
    [string]$AfterEffectsPayloadDirName = 'AEFT_26.2_win64',
    [string]$CreativeCloudHelperDirName = 'CreativeCloudHelper_win64',
    [string]$InstallRoot = 'C:\Program Files\Adobe\Adobe After Effects 2026',
    [string]$PatchScriptPath = 'C:\workspace\scripts\modify_setup_win.py',
    [string]$PythonBinary = 'python',
    [switch]$RequirePayload
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($AfterEffectsPayloadRoot)) {
    $AfterEffectsPayloadRoot = Join-Path $InstallerPayloadRoot $AfterEffectsPayloadDirName
}
if ([string]::IsNullOrWhiteSpace($CreativeCloudHelperRoot)) {
    $CreativeCloudHelperRoot = Join-Path $InstallerPayloadRoot $CreativeCloudHelperDirName
}

$driverXmlPath = Join-Path $AfterEffectsPayloadRoot 'driver.xml'
$helperSetupPath = Join-Path $CreativeCloudHelperRoot 'HDBox\Setup.exe'
$helperIpcPath = Join-Path $CreativeCloudHelperRoot 'IPC'
$targetRoot = 'C:\Program Files\Common Files\Adobe\Adobe Desktop Common'
$targetSetupPath = Join-Path $targetRoot 'HDBox\Setup.exe'
$aeRenderBinary = Join-Path $InstallRoot 'Support Files\aerender.exe'

if (Test-Path $aeRenderBinary) {
    Write-Host 'After Effects already installed.'
    & $aeRenderBinary -version
    return
}

$missingPaths = @(
    $driverXmlPath,
    $helperSetupPath,
    $helperIpcPath,
    $PatchScriptPath
) | Where-Object { -not (Test-Path $_) }

if ($missingPaths.Count -gt 0) {
    if ($RequirePayload) {
        throw "Missing required installer inputs:`n$($missingPaths -join "`n")"
    }

    Write-Host 'Skipping After Effects auto-install because installer payload is not mounted.'
    return
}

& $PythonBinary $PatchScriptPath $helperSetupPath
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to patch Setup.exe before container installation.'
}

New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null
Copy-Item (Join-Path $CreativeCloudHelperRoot 'HDBox') $targetRoot -Recurse -Force
Copy-Item $helperIpcPath $targetRoot -Recurse -Force

$process = Start-Process -FilePath $targetSetupPath -ArgumentList "--install=1 --driverXML=$driverXmlPath" -PassThru -Wait
if ($process.ExitCode -ne 0) {
    throw "Adobe Setup.exe exited with code $($process.ExitCode)."
}

if (-not (Test-Path $aeRenderBinary)) {
    throw "aerender.exe not found after installation at $aeRenderBinary"
}

& $aeRenderBinary -version