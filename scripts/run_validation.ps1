param(
    [string]$ImageTag = 'shotwright:dev',
    [string]$ContainerName = 'shotwright-validation'
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataRoot = Join-Path $ProjectRoot 'validation-data'
$AeRoot = 'C:\Program Files\Adobe\Adobe After Effects 2026'
$AeBinary = Join-Path $AeRoot 'Support Files\AfterFX.exe'
$AeRenderBinary = Join-Path $AeRoot 'Support Files\aerender.exe'

if (-not (Test-Path $AeBinary)) {
    throw "AfterFX.exe not found at $AeBinary"
}
if (-not (Test-Path $AeRenderBinary)) {
    throw "aerender.exe not found at $AeRenderBinary"
}

New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot 'output') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot 'templates') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot 'work') | Out-Null

$existingContainer = docker ps -a --filter "name=^/$ContainerName$" --format "{{.Names}}"
if ($existingContainer) {
    docker rm -f $ContainerName | Out-Null
}

docker run -d --name $ContainerName --isolation process `
    -v "${AeRoot}:${AeRoot}" `
    -v "${ProjectRoot}:C:\workspace" `
    -v "${DataRoot}:C:\data" `
    -w C:\workspace `
    $ImageTag powershell -NoProfile -Command "Start-Sleep -Seconds 36000" | Out-Null

try {
    docker exec $ContainerName powershell -NoProfile -Command "& { Remove-Item 'C:\data\templates\validation_motion.aep' -ErrorAction SilentlyContinue; `$proc = Start-Process -FilePath '$AeBinary' -ArgumentList '-r','C:\workspace\scripts\create_validation_animation_project.jsx' -PassThru; `$proc | Wait-Process -Timeout 300; if (-not (Test-Path 'C:\data\templates\validation_motion.aep')) { throw 'validation AEP not generated'; } }"

    docker exec $ContainerName powershell -NoProfile -Command "& { Remove-Item 'C:\data\output\validation.mp4' -ErrorAction SilentlyContinue; & nexrender-cli.cmd -f 'C:\workspace\scripts\validation_nexrender_job.json' -w 'C:\data\work' -b '$AeRenderBinary' --skip-cleanup --debug; exit `$LASTEXITCODE }"

    Get-Item (Join-Path $DataRoot 'output\validation.mp4') | Select-Object FullName, Length, LastWriteTime
}
finally {
    docker rm -f $ContainerName | Out-Null
}
