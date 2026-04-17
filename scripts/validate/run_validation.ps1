param(
    [string]$ImageTag = 'shotwright:dev',
    [string]$ContainerName = 'shotwright-validation',
    [string]$AfterEffectsPayloadRoot = '',
    [string]$CreativeCloudHelperRoot = '',
    [int]$InstallTimeoutSeconds = 1800
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataRoot = Join-Path $ProjectRoot 'validation-data'
$OutputMp4 = Join-Path $DataRoot 'output\validation.mp4'
$WorkRoot = Join-Path $DataRoot 'work'
$ContainerPayloadRoot = 'C:\lab\payload'
$ContainerAeRoot = 'C:\Program Files\Adobe\Adobe After Effects 2026'
$AeBinary = Join-Path $ContainerAeRoot 'Support Files\AfterFX.exe'
$AeRenderBinary = Join-Path $ContainerAeRoot 'Support Files\aerender.exe'
$UseInstallerPayload = -not [string]::IsNullOrWhiteSpace($AfterEffectsPayloadRoot) -or -not [string]::IsNullOrWhiteSpace($CreativeCloudHelperRoot)

if ($UseInstallerPayload -and (
    [string]::IsNullOrWhiteSpace($AfterEffectsPayloadRoot) -or
    [string]::IsNullOrWhiteSpace($CreativeCloudHelperRoot)
)) {
    throw 'When using installer payload mode, both -AfterEffectsPayloadRoot and -CreativeCloudHelperRoot are required.'
}

if (-not $UseInstallerPayload) {
    $HostAeRoot = 'C:\Program Files\Adobe\Adobe After Effects 2026'
    $HostAeBinary = Join-Path $HostAeRoot 'Support Files\AfterFX.exe'
    $HostAeRenderBinary = Join-Path $HostAeRoot 'Support Files\aerender.exe'
    if (-not (Test-Path $HostAeBinary)) {
        throw "AfterFX.exe not found at $HostAeBinary"
    }
    if (-not (Test-Path $HostAeRenderBinary)) {
        throw "aerender.exe not found at $HostAeRenderBinary"
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot 'output') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot 'templates') | Out-Null
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null

function Get-ContainerLogsText {
    param([string]$Name)

    return (docker logs $Name 2>&1 | Out-String)
}

function Find-LatestRenderedMp4 {
    param([string]$Root)

    return Get-ChildItem -Path $Root -Filter 'result.mp4' -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

function Wait-ForAfterEffectsInstall {
    param(
        [string]$Name,
        [string]$AeRenderBinaryPath,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $isRunning = docker inspect --format '{{.State.Running}}' $Name 2>$null
        $isRunningText = if ($null -eq $isRunning) { '' } else { $isRunning.ToString().Trim() }
        if ($LASTEXITCODE -ne 0 -or $isRunningText -ne 'true') {
            throw "Validation container exited before After Effects finished installing.`n$(Get-ContainerLogsText -Name $Name)"
        }

        $isReady = docker exec $Name powershell -NoProfile -Command "if (Test-Path '$AeRenderBinaryPath') { 'ready' }" 2>$null
        $isReadyText = if ($null -eq $isReady) { '' } else { $isReady.ToString().Trim() }
        if ($LASTEXITCODE -eq 0 -and $isReadyText -eq 'ready') {
            $versionOutput = docker exec $Name powershell -NoProfile -Command "& '$AeRenderBinaryPath' -version" 2>&1
            $versionText = if ($null -eq $versionOutput) { '' } else { $versionOutput.ToString() }
            if ($versionText -notmatch 'aerender version') {
                throw 'aerender.exe exists but failed to report a version.'
            }
            return
        }

        Start-Sleep -Seconds 10
    }

    throw "Timed out waiting for After Effects to install inside the container.`n$(Get-ContainerLogsText -Name $Name)"
}

$existingContainer = docker ps -a --filter "name=^/$ContainerName$" --format "{{.Names}}"
if ($existingContainer) {
    docker rm -f $ContainerName | Out-Null
}

$dockerArgs = @(
    'run',
    '-d',
    '--name', $ContainerName,
    '--isolation', 'process',
    '-v', "${ProjectRoot}:C:\workspace",
    '-v', "${DataRoot}:C:\data",
    '-w', 'C:\workspace'
)

if ($UseInstallerPayload) {
    if (-not (Test-Path $AfterEffectsPayloadRoot)) {
        throw "After Effects payload root not found at $AfterEffectsPayloadRoot"
    }
    if (-not (Test-Path $CreativeCloudHelperRoot)) {
        throw "Creative Cloud helper root not found at $CreativeCloudHelperRoot"
    }

    $dockerArgs += @('-v', "${AfterEffectsPayloadRoot}:${ContainerPayloadRoot}\AEFT_26.2_win64")
    $dockerArgs += @('-v', "${CreativeCloudHelperRoot}:${ContainerPayloadRoot}\CreativeCloudHelper_win64")
} else {
    $dockerArgs += @('-v', "${HostAeRoot}:${ContainerAeRoot}")
}

$dockerArgs += $ImageTag
docker @dockerArgs | Out-Null

try {
    if ($UseInstallerPayload) {
        Wait-ForAfterEffectsInstall -Name $ContainerName -AeRenderBinaryPath $AeRenderBinary -TimeoutSeconds $InstallTimeoutSeconds
    }

    docker exec $ContainerName powershell -NoProfile -Command "& { Remove-Item 'C:\data\templates\validation_motion.aep' -ErrorAction SilentlyContinue; `$proc = Start-Process -FilePath '$AeBinary' -ArgumentList '-r','C:\workspace\scripts\validate\create_validation_animation_project.jsx' -PassThru; `$proc | Wait-Process -Timeout 300; if (-not (Test-Path 'C:\data\templates\validation_motion.aep')) { throw 'validation AEP not generated'; } }"

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    docker exec $ContainerName powershell -NoProfile -Command "& { Remove-Item 'C:\data\output\validation.mp4' -ErrorAction SilentlyContinue; & nexrender-cli.cmd -f 'C:\workspace\scripts\validate\validation_nexrender_job.json' -w 'C:\data\work' -b '$AeRenderBinary' --skip-cleanup --debug; exit `$LASTEXITCODE }"
    $renderExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference

    if (-not (Test-Path $OutputMp4)) {
        $fallbackResult = Find-LatestRenderedMp4 -Root $WorkRoot
        if ($null -ne $fallbackResult) {
            Copy-Item $fallbackResult.FullName $OutputMp4 -Force
        }
    }

    if (-not (Test-Path $OutputMp4)) {
        throw 'validation.mp4 was not produced.'
    }

    if ($renderExitCode -ne 0) {
        Write-Warning "nexrender exited with code $renderExitCode, but validation.mp4 was recovered from the work directory."
    }

    Get-Item $OutputMp4 | Select-Object FullName, Length, LastWriteTime
}
finally {
    docker rm -f $ContainerName | Out-Null
}
