$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $ProjectRoot 'shotwright-config.json'
if (-not (Test-Path $ConfigPath)) {
    throw "Shotwright config not found at $ConfigPath"
}

$ShotwrightConfig = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$ContainerPaths = $ShotwrightConfig.paths.windowsContainer
$DefaultInstallerPayloadRoot = Join-Path $ContainerPaths.dataRoot $ContainerPaths.payloadDirName
$InstallScriptPath = Join-Path $ProjectRoot 'scripts\install\install_after_effects_in_container.ps1'
$KeepaliveScriptPath = Join-Path $ProjectRoot 'keepalive.ps1'

function Test-FlagEnabled {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }

    switch ($Value.Trim().ToLowerInvariant()) {
        '0' { return $false }
        'false' { return $false }
        'no' { return $false }
        default { return $true }
    }
}

$autoInstallValue = if (-not [string]::IsNullOrWhiteSpace($env:SHOTWRIGHT_AUTO_INSTALL_AFTER_EFFECTS)) {
    $env:SHOTWRIGHT_AUTO_INSTALL_AFTER_EFFECTS
} else {
    $env:AUTO_INSTALL_AFTER_EFFECTS
}

if (Test-FlagEnabled $autoInstallValue) {
    $payloadRoot = if (-not [string]::IsNullOrWhiteSpace($env:SHOTWRIGHT_INSTALLER_PAYLOAD_ROOT)) {
        $env:SHOTWRIGHT_INSTALLER_PAYLOAD_ROOT
    } else {
        $DefaultInstallerPayloadRoot
    }

    & $InstallScriptPath -InstallerPayloadRoot $payloadRoot
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $KeepaliveScriptPath