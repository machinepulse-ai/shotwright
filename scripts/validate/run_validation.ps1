param(
    [string]$ImageTag = '',
    [string]$ContainerName = '',
    [string]$AfterEffectsPayloadRoot = '',
    [string]$CreativeCloudHelperRoot = '',
    [string]$HostAeRoot = '',
    [string]$ContainerAeRoot = '',
    [string]$PythonBinary = 'python',
    [int]$InstallTimeoutSeconds = 1800
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $ProjectRoot 'shotwright-config.json'
if (-not (Test-Path $ConfigPath)) {
    throw "Shotwright config not found at $ConfigPath"
}

$ShotwrightConfig = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$WorkspaceConfig = $ShotwrightConfig.workspace
$ContainerPaths = $ShotwrightConfig.paths.windowsContainer

if ([string]::IsNullOrWhiteSpace($ImageTag)) {
    $ImageTag = 'shotwright:dev'
}
if ([string]::IsNullOrWhiteSpace($ContainerName)) {
    $ContainerName = 'shotwright-validation'
}

$DataRoot = Join-Path $ProjectRoot $WorkspaceConfig.validationDataDirName
$TemplatesRoot = Join-Path $DataRoot $WorkspaceConfig.templatesDirName
$OutputRoot = Join-Path $DataRoot $WorkspaceConfig.outputDirName
$WorkRoot = Join-Path $DataRoot $WorkspaceConfig.workDirName
$OutputMp4 = Join-Path $OutputRoot $WorkspaceConfig.validationOutputFileName
$GeneratedValidationJobPath = Join-Path $WorkRoot 'validation_nexrender_job.generated.json'
$ValidationJobTemplatePath = Join-Path $ProjectRoot 'scripts\validate\validation_nexrender_job.json'
$ContainerPayloadRoot = Join-Path $ContainerPaths.dataRoot $ContainerPaths.payloadDirName
$ContainerTemplatesRoot = Join-Path $ContainerPaths.dataRoot $ContainerPaths.templatesDirName
$ContainerOutputRoot = Join-Path $ContainerPaths.dataRoot $ContainerPaths.outputDirName
$ContainerWorkRoot = Join-Path $ContainerPaths.dataRoot $ContainerPaths.workDirName
$ContainerValidationProjectPath = Join-Path $ContainerTemplatesRoot $WorkspaceConfig.validationProjectFileName
$ContainerValidationOutputPath = Join-Path $ContainerOutputRoot $WorkspaceConfig.validationOutputFileName
$ContainerGeneratedValidationJobPath = Join-Path $ContainerWorkRoot 'validation_nexrender_job.generated.json'
$ContainerValidationPatchPath = Join-Path $ContainerPaths.repositoryRoot 'scripts\validate\validation_patch.jsx'
$ContainerValidationProjectScriptPath = Join-Path $ContainerPaths.repositoryRoot 'scripts\validate\create_validation_animation_project.jsx'
$ValidationYearText = Get-Date -Format 'yyyy'
$UseInstallerPayload = -not [string]::IsNullOrWhiteSpace($AfterEffectsPayloadRoot) -or -not [string]::IsNullOrWhiteSpace($CreativeCloudHelperRoot)
$ContainerAfterEffectsPayloadDirName = ''
$ContainerCreativeCloudHelperDirName = ''
$SetupVersionsScriptPath = Join-Path $ProjectRoot 'scripts\install\setup_versions.py'
$SetupVersionsConfigPath = Join-Path $ProjectRoot 'setup-versions.yml'

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
        return (Join-Path $ContainerPaths.adobeInstallBaseRoot $InstallDirectoryName)
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

    return (Join-Path $ContainerPaths.adobeInstallBaseRoot "Adobe After Effects $(2000 + $majorVersion)")
}

function Find-LatestInstalledAfterEffectsRoot {
    if (-not (Test-Path $ContainerPaths.adobeInstallBaseRoot)) {
        return ''
    }

    $candidate = Get-ChildItem -Path $ContainerPaths.adobeInstallBaseRoot -Directory -Filter 'Adobe After Effects *' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1

    if ($null -eq $candidate) {
        return ''
    }

    return $candidate.FullName
}

function Convert-ToFileUri {
    param([string]$WindowsPath)

    $normalized = $WindowsPath -replace '\\', '/'
    return "file:///$normalized"
}

function Convert-ToForwardSlashPath {
    param([string]$WindowsPath)

    return ($WindowsPath -replace '\\', '/')
}

function Write-ValidationJobFile {
    param(
        [string]$TemplatePath,
        [string]$OutputPath,
        [string]$TemplateSrc,
        [string]$PatchScriptSrc,
        [string]$OutputFile,
        [string]$YearText
    )

    $template = Get-Content -Raw $TemplatePath
    $replacements = @{
        '__TEMPLATE_SRC__' = Convert-ToFileUri $TemplateSrc
        '__PATCH_SCRIPT_SRC__' = Convert-ToFileUri $PatchScriptSrc
        '__OUTPUT_FILE__' = Convert-ToForwardSlashPath $OutputFile
        '__COMP_NAME__' = 'main'
        '__OUTPUT_EXT__' = 'mp4'
        '__DURATION__' = '4'
        '__TEXT_MAIN__' = 'NEXRENDER OK'
        '__TEXT_SUB__' = 'cloud container smoke test'
        '__TEXT_YEAR__' = [string]$YearText
    }

    foreach ($key in $replacements.Keys) {
        $template = $template.Replace($key, $replacements[$key])
    }

    Set-Content -Path $OutputPath -Value $template -Encoding utf8
}

if ($UseInstallerPayload -and (
    [string]::IsNullOrWhiteSpace($AfterEffectsPayloadRoot) -or
    [string]::IsNullOrWhiteSpace($CreativeCloudHelperRoot)
)) {
    throw 'When using installer payload mode, both -AfterEffectsPayloadRoot and -CreativeCloudHelperRoot are required.'
}

if ($UseInstallerPayload) {
    $ContainerAfterEffectsPayloadDirName = Split-Path -Leaf $AfterEffectsPayloadRoot
    $ContainerCreativeCloudHelperDirName = Split-Path -Leaf $CreativeCloudHelperRoot
    if ([string]::IsNullOrWhiteSpace($ContainerAfterEffectsPayloadDirName)) {
        throw 'Unable to determine the After Effects payload directory name from -AfterEffectsPayloadRoot.'
    }
    if ([string]::IsNullOrWhiteSpace($ContainerCreativeCloudHelperDirName)) {
        throw 'Unable to determine the Creative Cloud helper directory name from -CreativeCloudHelperRoot.'
    }
}

$ResolvedInstallRoot = ''
if (-not [string]::IsNullOrWhiteSpace($ContainerAeRoot)) {
    $ResolvedInstallRoot = $ContainerAeRoot
}
else {
    $ResolvedInstallRoot = Get-SetupVersionsField -Field 'install_root'
}

if ([string]::IsNullOrWhiteSpace($ResolvedInstallRoot)) {
    $payloadVersion = Get-AfterEffectsVersionFromPayloadDirName -DirectoryName $ContainerAfterEffectsPayloadDirName
    $installDirName = Get-SetupVersionsField -Field 'install_dir_name'
    $ResolvedInstallRoot = Resolve-AfterEffectsInstallRoot -Version $payloadVersion -InstallDirectoryName $installDirName
}

if ([string]::IsNullOrWhiteSpace($ResolvedInstallRoot)) {
    $ResolvedInstallRoot = Find-LatestInstalledAfterEffectsRoot
}

if ([string]::IsNullOrWhiteSpace($ResolvedInstallRoot)) {
    throw 'Unable to determine the After Effects install root from setup-versions.yml, the payload directory name, or an explicit -HostAeRoot/-ContainerAeRoot override.'
}

$setupReleaseYear = Get-SetupVersionsField -Field 'release_year'
if (-not [string]::IsNullOrWhiteSpace($setupReleaseYear)) {
    $ValidationYearText = $setupReleaseYear
}

if ([string]::IsNullOrWhiteSpace($ContainerAeRoot)) {
    $ContainerAeRoot = $ResolvedInstallRoot
}
if ([string]::IsNullOrWhiteSpace($HostAeRoot)) {
    $HostAeRoot = $ResolvedInstallRoot
}

$AeBinary = Join-Path $ContainerAeRoot 'Support Files\AfterFX.exe'
$AeRenderBinary = Join-Path $ContainerAeRoot 'Support Files\aerender.exe'

if (-not $UseInstallerPayload) {
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
New-Item -ItemType Directory -Force -Path $TemplatesRoot | Out-Null
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
Write-ValidationJobFile -TemplatePath $ValidationJobTemplatePath -OutputPath $GeneratedValidationJobPath -TemplateSrc $ContainerValidationProjectPath -PatchScriptSrc $ContainerValidationPatchPath -OutputFile $ContainerValidationOutputPath -YearText $ValidationYearText

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
    '-e', "SHOTWRIGHT_TEMPLATES_ROOT=$ContainerTemplatesRoot",
    '-e', "SHOTWRIGHT_VALIDATION_YEAR=$ValidationYearText",
    '-v', "${ProjectRoot}:$($ContainerPaths.repositoryRoot)",
    '-v', "${DataRoot}:$($ContainerPaths.dataRoot)",
    '-w', $ContainerPaths.repositoryRoot
)

if ($UseInstallerPayload) {
    if (-not (Test-Path $AfterEffectsPayloadRoot)) {
        throw "After Effects payload root not found at $AfterEffectsPayloadRoot"
    }
    if (-not (Test-Path $CreativeCloudHelperRoot)) {
        throw "Creative Cloud helper root not found at $CreativeCloudHelperRoot"
    }

    $dockerArgs += @('-e', "SHOTWRIGHT_AFTER_EFFECTS_PAYLOAD_DIR_NAME=$ContainerAfterEffectsPayloadDirName")
    $dockerArgs += @('-e', "SHOTWRIGHT_CREATIVE_CLOUD_HELPER_DIR_NAME=$ContainerCreativeCloudHelperDirName")
    $dockerArgs += @('-e', "SHOTWRIGHT_INSTALL_ROOT=$ContainerAeRoot")
    $dockerArgs += @('-v', "${AfterEffectsPayloadRoot}:${ContainerPayloadRoot}\$ContainerAfterEffectsPayloadDirName")
    $dockerArgs += @('-v', "${CreativeCloudHelperRoot}:${ContainerPayloadRoot}\$ContainerCreativeCloudHelperDirName")
} else {
    $dockerArgs += @('-v', "${HostAeRoot}:${ContainerAeRoot}")
}

$dockerArgs += $ImageTag
docker @dockerArgs | Out-Null

try {
    if ($UseInstallerPayload) {
        Wait-ForAfterEffectsInstall -Name $ContainerName -AeRenderBinaryPath $AeRenderBinary -TimeoutSeconds $InstallTimeoutSeconds
    }

    docker exec $ContainerName powershell -NoProfile -Command "& { Remove-Item '$ContainerValidationProjectPath' -ErrorAction SilentlyContinue; `$proc = Start-Process -FilePath '$AeBinary' -ArgumentList '-r','$ContainerValidationProjectScriptPath' -PassThru; `$proc | Wait-Process -Timeout 300; if (-not (Test-Path '$ContainerValidationProjectPath')) { throw 'validation AEP not generated'; } }"

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    docker exec $ContainerName powershell -NoProfile -Command "& { Remove-Item '$ContainerValidationOutputPath' -ErrorAction SilentlyContinue; & nexrender-cli.cmd -f '$ContainerGeneratedValidationJobPath' -w '$ContainerWorkRoot' -b '$AeRenderBinary' --skip-cleanup --debug; exit `$LASTEXITCODE }"
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
