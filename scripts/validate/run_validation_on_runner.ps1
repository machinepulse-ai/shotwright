param(
    [string]$AfterEffectsPayloadRoot = '',
    [string]$CreativeCloudHelperRoot = '',
    [string]$PythonBinary = 'python',
    [string]$NexrenderBinary = 'nexrender-cli.cmd',
    [int]$AfterFxTimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$ConfigPath = Join-Path $ProjectRoot 'shotwright-config.json'
if (-not (Test-Path $ConfigPath)) {
    throw "Shotwright config not found at $ConfigPath"
}

$ShotwrightConfig = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$WorkspaceConfig = $ShotwrightConfig.workspace
$WindowsPaths = $ShotwrightConfig.paths.windowsContainer

$DataRoot = Join-Path $ProjectRoot $WorkspaceConfig.validationDataDirName
$TemplatesRoot = Join-Path $DataRoot $WorkspaceConfig.templatesDirName
$OutputRoot = Join-Path $DataRoot $WorkspaceConfig.outputDirName
$WorkRoot = Join-Path $DataRoot $WorkspaceConfig.workDirName
$ValidationProjectPath = Join-Path $TemplatesRoot $WorkspaceConfig.validationProjectFileName
$OutputMp4 = Join-Path $OutputRoot $WorkspaceConfig.validationOutputFileName
$GeneratedValidationJobPath = Join-Path $WorkRoot 'validation_nexrender_job.generated.json'
$ValidationJobTemplatePath = Join-Path $ProjectRoot 'scripts\validate\validation_nexrender_job.json'
$ValidationPatchPath = Join-Path $ProjectRoot 'scripts\validate\validation_patch.jsx'
$ValidationProjectScriptPath = Join-Path $ProjectRoot 'scripts\validate\create_validation_animation_project.jsx'
$InstallScriptPath = Join-Path $ProjectRoot 'scripts\install\install_after_effects_in_container.ps1'
$SetupVersionsScriptPath = Join-Path $ProjectRoot 'scripts\install\setup_versions.py'
$SetupVersionsConfigPath = Join-Path $ProjectRoot 'setup-versions.yml'
$ValidationYearText = Get-Date -Format 'yyyy'

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
        return (Join-Path $WindowsPaths.adobeInstallBaseRoot $InstallDirectoryName)
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

    return (Join-Path $WindowsPaths.adobeInstallBaseRoot "Adobe After Effects $(2000 + $majorVersion)")
}

function Find-LatestInstalledAfterEffectsRoot {
    if (-not (Test-Path $WindowsPaths.adobeInstallBaseRoot)) {
        return ''
    }

    $candidate = Get-ChildItem -Path $WindowsPaths.adobeInstallBaseRoot -Directory -Filter 'Adobe After Effects *' -ErrorAction SilentlyContinue |
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
        '__TEXT_SUB__' = 'runner host smoke test'
        '__TEXT_YEAR__' = [string]$YearText
    }

    foreach ($key in $replacements.Keys) {
        $template = $template.Replace($key, $replacements[$key])
    }

    Set-Content -Path $OutputPath -Value $template -Encoding utf8
}

function Find-LatestRenderedMp4 {
    param([string]$Root)

    return Get-ChildItem -Path $Root -Filter 'result.mp4' -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

if ([string]::IsNullOrWhiteSpace($AfterEffectsPayloadRoot) -or [string]::IsNullOrWhiteSpace($CreativeCloudHelperRoot)) {
    throw 'Both -AfterEffectsPayloadRoot and -CreativeCloudHelperRoot are required for runner validation.'
}

$AfterEffectsPayloadDirName = Split-Path -Leaf $AfterEffectsPayloadRoot
$ResolvedInstallRoot = Get-SetupVersionsField -Field 'install_root'
if ([string]::IsNullOrWhiteSpace($ResolvedInstallRoot)) {
    $payloadVersion = Get-AfterEffectsVersionFromPayloadDirName -DirectoryName $AfterEffectsPayloadDirName
    $installDirName = Get-SetupVersionsField -Field 'install_dir_name'
    $ResolvedInstallRoot = Resolve-AfterEffectsInstallRoot -Version $payloadVersion -InstallDirectoryName $installDirName
}
if ([string]::IsNullOrWhiteSpace($ResolvedInstallRoot)) {
    $ResolvedInstallRoot = Find-LatestInstalledAfterEffectsRoot
}
if ([string]::IsNullOrWhiteSpace($ResolvedInstallRoot)) {
    throw 'Unable to determine the After Effects install root for runner validation.'
}

$setupReleaseYear = Get-SetupVersionsField -Field 'release_year'
if (-not [string]::IsNullOrWhiteSpace($setupReleaseYear)) {
    $ValidationYearText = $setupReleaseYear
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $InstallScriptPath `
    -AfterEffectsPayloadRoot $AfterEffectsPayloadRoot `
    -CreativeCloudHelperRoot $CreativeCloudHelperRoot `
    -PythonBinary $PythonBinary `
    -RequirePayload
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install After Effects on the runner.'
}

$AeBinary = Join-Path $ResolvedInstallRoot 'Support Files\AfterFX.exe'
$AeRenderBinary = Join-Path $ResolvedInstallRoot 'Support Files\aerender.exe'
if (-not (Test-Path $AeBinary)) {
    throw "AfterFX.exe not found at $AeBinary"
}
if (-not (Test-Path $AeRenderBinary)) {
    throw "aerender.exe not found at $AeRenderBinary"
}

Get-Command $NexrenderBinary -ErrorAction Stop | Out-Null

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $TemplatesRoot | Out-Null
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null

$previousTemplatesRoot = $env:SHOTWRIGHT_TEMPLATES_ROOT
$previousValidationYear = $env:SHOTWRIGHT_VALIDATION_YEAR
try {
    $env:SHOTWRIGHT_TEMPLATES_ROOT = $TemplatesRoot
    $env:SHOTWRIGHT_VALIDATION_YEAR = $ValidationYearText

    Remove-Item $ValidationProjectPath -ErrorAction SilentlyContinue
    $projectProcess = Start-Process -FilePath $AeBinary -ArgumentList '-r', $ValidationProjectScriptPath -PassThru
    $projectProcess | Wait-Process -Timeout $AfterFxTimeoutSeconds
}
finally {
    $env:SHOTWRIGHT_TEMPLATES_ROOT = $previousTemplatesRoot
    $env:SHOTWRIGHT_VALIDATION_YEAR = $previousValidationYear
}

if (-not (Test-Path $ValidationProjectPath)) {
    throw 'validation AEP was not generated on the runner.'
}

Write-ValidationJobFile `
    -TemplatePath $ValidationJobTemplatePath `
    -OutputPath $GeneratedValidationJobPath `
    -TemplateSrc $ValidationProjectPath `
    -PatchScriptSrc $ValidationPatchPath `
    -OutputFile $OutputMp4 `
    -YearText $ValidationYearText

Remove-Item $OutputMp4 -ErrorAction SilentlyContinue

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $NexrenderBinary -f $GeneratedValidationJobPath -w $WorkRoot -b $AeRenderBinary --skip-cleanup --debug
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