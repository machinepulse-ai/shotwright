<#
.SYNOPSIS
    One-click deployment for Shotwright platform (Windows containers).
.DESCRIPTION
    Builds and starts all services via Docker Compose.
    Pass -Dev to enable hot-reload development mode.
.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -Dev
    .\deploy.ps1 -Build
#>
param(
    [switch]$Dev,
    [switch]$Build,
    [switch]$Detach
)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot\..

# Ensure .env exists
if (-not (Test-Path '.env')) {
    Write-Host '[deploy] Creating .env from .env.example ...' -ForegroundColor Cyan
    Copy-Item '.env.example' '.env'
}

$composeFiles = @('-f', 'docker-compose.yml')
if ($Dev) {
    $composeFiles += @('-f', 'docker-compose.dev.yml')
    Write-Host '[deploy] Development mode enabled (hot-reload)' -ForegroundColor Yellow
}

$upArgs = @('up')
if ($Build) { $upArgs += '--build' }
if ($Detach) { $upArgs += '-d' }

Write-Host '[deploy] Starting Shotwright platform ...' -ForegroundColor Green
docker compose @composeFiles @upArgs

Pop-Location
