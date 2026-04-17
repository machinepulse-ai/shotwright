<#
.SYNOPSIS
    Garbage cleanup for Shotwright platform.
.DESCRIPTION
    Stops all services, removes containers, volumes, and dangling images.
    Pass -All to also remove named volumes (destroys MongoDB data!).
.EXAMPLE
    .\cleanup.ps1           # stop + remove containers
    .\cleanup.ps1 -All      # also remove volumes and dangling images
    .\cleanup.ps1 -Prune    # full Docker system prune
#>
param(
    [switch]$All,
    [switch]$Prune
)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot\..

Write-Host '[cleanup] Stopping Shotwright services ...' -ForegroundColor Cyan
docker compose down

# Remove orphaned shotwright AE containers
$aeContainers = docker ps -a --filter "name=shotwright-" --format "{{.ID}}" 2>$null
if ($aeContainers) {
    Write-Host "[cleanup] Removing $($aeContainers.Count) shotwright AE container(s) ..." -ForegroundColor Yellow
    $aeContainers | ForEach-Object { docker rm -f $_ }
}

if ($All) {
    Write-Host '[cleanup] Removing named volumes ...' -ForegroundColor Red
    docker compose down -v

    Write-Host '[cleanup] Removing dangling images ...' -ForegroundColor Yellow
    docker image prune -f --filter "label=app.kubernetes.io/part-of=shotwright"
}

if ($Prune) {
    Write-Host '[cleanup] Running full Docker system prune ...' -ForegroundColor Red
    docker system prune -f
}

Write-Host '[cleanup] Done.' -ForegroundColor Green
Pop-Location
