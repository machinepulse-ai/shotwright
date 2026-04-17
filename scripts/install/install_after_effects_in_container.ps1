param(
    [string]$InstallerPayloadRoot = '',
    [string]$AfterEffectsPayloadRoot = '',
    [string]$CreativeCloudHelperRoot = '',
    [string]$AfterEffectsPayloadDirName = '',
    [string]$CreativeCloudHelperDirName = '',
    [string]$InstallRoot = '',
    [string]$SetupVersionsConfigPath = '',
    [string]$SetupVersionsScriptPath = '',
    [string]$PatchScriptPath = '',
    [string]$PythonBinary = 'python',
    [switch]$RequirePayload
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ConfigPath = Join-Path $ProjectRoot 'shotwright-config.json'
if (-not (Test-Path $ConfigPath)) {
    throw "Shotwright config not found at $ConfigPath"
}

$ShotwrightConfig = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$ContainerPaths = $ShotwrightConfig.paths.windowsContainer
$AdobeInstallBaseRoot = $ContainerPaths.adobeInstallBaseRoot

if ([string]::IsNullOrWhiteSpace($InstallerPayloadRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($env:SHOTWRIGHT_INSTALLER_PAYLOAD_ROOT)) {
        $InstallerPayloadRoot = $env:SHOTWRIGHT_INSTALLER_PAYLOAD_ROOT
    }
    else {
        $InstallerPayloadRoot = Join-Path $ContainerPaths.dataRoot $ContainerPaths.payloadDirName
    }
}

if ([string]::IsNullOrWhiteSpace($SetupVersionsConfigPath)) {
    $SetupVersionsConfigPath = Join-Path $ProjectRoot 'setup-versions.yml'
}
if ([string]::IsNullOrWhiteSpace($SetupVersionsScriptPath)) {
    $SetupVersionsScriptPath = Join-Path $ProjectRoot 'scripts\install\setup_versions.py'
}
if ([string]::IsNullOrWhiteSpace($PatchScriptPath)) {
    $PatchScriptPath = Join-Path $ProjectRoot 'scripts\install\modify_setup_win.py'
}

function Find-InstallerDirectoryName {
    param(
        [string]$Root,
        [string]$Filter
    )

    if (-not (Test-Path $Root)) {
        return ''
    }

    $candidate = Get-ChildItem -Path $Root -Directory -Filter $Filter -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1

    if ($null -eq $candidate) {
        return ''
    }

    return $candidate.Name
}

function Get-SetupVersionsField {
    param([string]$Field)

    if (-not (Test-Path $SetupVersionsScriptPath) -or -not (Test-Path $SetupVersionsConfigPath)) {
        return ''
    }

    try {
        $value = & $PythonBinary $SetupVersionsScriptPath --config $SetupVersionsConfigPath --field $Field 2>$null
        if ($LASTEXITCODE -ne 0 -or $null -eq $value) {
            return ''
        }

        return $value.ToString().Trim()
    }
    catch {
        return ''
    }
}

function Get-AfterEffectsVersionFromPayloadDirName {
    param([string]$DirectoryName)

    if ([string]::IsNullOrWhiteSpace($DirectoryName)) {
        return ''
    }

    $match = [regex]::Match($DirectoryName, '^[^_]+_(?<version>\d+(?:\.\d+)*)_[^_]+$')
    if (-not $match.Success) {
        return ''
    }

    return $match.Groups['version'].Value
}

function Resolve-AfterEffectsInstallRoot {
    param(
        [string]$Version,
        [string]$InstallDirectoryName
    )

    if (-not [string]::IsNullOrWhiteSpace($InstallDirectoryName)) {
        return (Join-Path $script:AdobeInstallBaseRoot $InstallDirectoryName)
    }

    if ([string]::IsNullOrWhiteSpace($Version)) {
        return ''
    }

    $majorText = $Version.Split('.', 2)[0]
    $majorVersion = 0
    if (-not [int]::TryParse($majorText, [ref]$majorVersion)) {
        return ''
    }

    if ($majorVersion -lt 10) {
        return ''
    }

    return (Join-Path $script:AdobeInstallBaseRoot "Adobe After Effects $(2000 + $majorVersion)")
}

function Find-LatestInstalledAfterEffectsRoot {
    if (-not (Test-Path $script:AdobeInstallBaseRoot)) {
        return ''
    }

    $candidate = Get-ChildItem -Path $script:AdobeInstallBaseRoot -Directory -Filter 'Adobe After Effects *' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1

    if ($null -eq $candidate) {
        return ''
    }

    return $candidate.FullName
}

if ([string]::IsNullOrWhiteSpace($AfterEffectsPayloadDirName)) {
    if (-not [string]::IsNullOrWhiteSpace($env:SHOTWRIGHT_AFTER_EFFECTS_PAYLOAD_DIR_NAME)) {
        $AfterEffectsPayloadDirName = $env:SHOTWRIGHT_AFTER_EFFECTS_PAYLOAD_DIR_NAME
    } elseif (-not [string]::IsNullOrWhiteSpace($AfterEffectsPayloadRoot)) {
        $AfterEffectsPayloadDirName = Split-Path -Leaf $AfterEffectsPayloadRoot
    } else {
        $AfterEffectsPayloadDirName = Find-InstallerDirectoryName -Root $InstallerPayloadRoot -Filter 'AEFT_*'
    }
}

if ([string]::IsNullOrWhiteSpace($CreativeCloudHelperDirName)) {
    if (-not [string]::IsNullOrWhiteSpace($env:SHOTWRIGHT_CREATIVE_CLOUD_HELPER_DIR_NAME)) {
        $CreativeCloudHelperDirName = $env:SHOTWRIGHT_CREATIVE_CLOUD_HELPER_DIR_NAME
    } elseif (-not [string]::IsNullOrWhiteSpace($CreativeCloudHelperRoot)) {
        $CreativeCloudHelperDirName = Split-Path -Leaf $CreativeCloudHelperRoot
    } else {
        $CreativeCloudHelperDirName = Find-InstallerDirectoryName -Root $InstallerPayloadRoot -Filter 'CreativeCloudHelper_*'
    }
}

if ([string]::IsNullOrWhiteSpace($AfterEffectsPayloadRoot) -and -not [string]::IsNullOrWhiteSpace($AfterEffectsPayloadDirName)) {
    $AfterEffectsPayloadRoot = Join-Path $InstallerPayloadRoot $AfterEffectsPayloadDirName
}
if ([string]::IsNullOrWhiteSpace($CreativeCloudHelperRoot) -and -not [string]::IsNullOrWhiteSpace($CreativeCloudHelperDirName)) {
    $CreativeCloudHelperRoot = Join-Path $InstallerPayloadRoot $CreativeCloudHelperDirName
}

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($env:SHOTWRIGHT_INSTALL_ROOT)) {
        $InstallRoot = $env:SHOTWRIGHT_INSTALL_ROOT
    }
    else {
        $InstallRoot = Get-SetupVersionsField -Field 'install_root'
        if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
            $payloadVersion = Get-AfterEffectsVersionFromPayloadDirName -DirectoryName $AfterEffectsPayloadDirName
            $installDirName = Get-SetupVersionsField -Field 'install_dir_name'
            $InstallRoot = Resolve-AfterEffectsInstallRoot -Version $payloadVersion -InstallDirectoryName $installDirName
        }
    }
}

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Find-LatestInstalledAfterEffectsRoot
}

$driverXmlPath = Join-Path $AfterEffectsPayloadRoot 'driver.xml'
$helperSetupPath = Join-Path $CreativeCloudHelperRoot 'HDBox\Setup.exe'
$helperIpcPath = Join-Path $CreativeCloudHelperRoot 'IPC'
$targetRoot = $ContainerPaths.desktopCommonRoot
$targetSetupPath = Join-Path $targetRoot 'HDBox\Setup.exe'
$aeRenderBinary = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    ''
} else {
    Join-Path $InstallRoot 'Support Files\aerender.exe'
}

if (-not [string]::IsNullOrWhiteSpace($aeRenderBinary) -and (Test-Path $aeRenderBinary)) {
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

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    throw 'Unable to determine the After Effects install root from SHOTWRIGHT_INSTALL_ROOT, setup-versions.yml, or the payload directory name.'
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