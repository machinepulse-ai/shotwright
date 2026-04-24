<#
.SYNOPSIS
    Local development without Docker — runs backend and frontend directly.
.DESCRIPTION
    Starts MongoDB (expects it on localhost:27017 or via SHOTWRIGHT_MONGO_URI),
    the FastAPI backend with uvicorn --reload, and the React dev server.
.EXAMPLE
    .\dev.ps1
#>

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$repoRoot = Split-Path $root -Parent

$verifySkillsSsl = $true
if ($env:SHOTWRIGHT_SKILLS_VERIFY_SSL) {
    $normalizedSkillsVerifySsl = $env:SHOTWRIGHT_SKILLS_VERIFY_SSL.Trim().ToLowerInvariant()
    if (@('0', 'false', 'no', 'off') -contains $normalizedSkillsVerifySsl) {
        $verifySkillsSsl = $false
    }
}

$skillsDownloadArgs = @(
    "$repoRoot\scripts\skills\download_skills_bundle.py",
    '--install-root', $repoRoot,
    '--no-progress'
)
if ($env:SHOTWRIGHT_GITHUB_TOKEN) {
    $skillsDownloadArgs += '--token-env'
    $skillsDownloadArgs += 'SHOTWRIGHT_GITHUB_TOKEN'
}
elseif ($env:GITHUB_TOKEN) {
    $skillsDownloadArgs += '--token-env'
    $skillsDownloadArgs += 'GITHUB_TOKEN'
}
if (-not $verifySkillsSsl) {
    $skillsDownloadArgs += '--no-verify-ssl'
}

Write-Host '[dev] Hydrating repo skills into .github/skills ...' -ForegroundColor Green
& python @skillsDownloadArgs
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to hydrate .github/skills before starting development services.'
}

# Backend
Write-Host '[dev] Starting backend (uvicorn) ...' -ForegroundColor Green
$backendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    $env:SHOTWRIGHT_MONGO_URI = 'mongodb://localhost:27017'
    $env:SHOTWRIGHT_DEBUG = 'true'
    if (-not $env:WATCHFILES_FORCE_POLLING) {
        $env:WATCHFILES_FORCE_POLLING = '1'
    }
    if (-not $env:WATCHFILES_POLL_DELAY_MS) {
        $env:WATCHFILES_POLL_DELAY_MS = '500'
    }
    if (-not $env:PYTHONPATH) {
        $env:PYTHONPATH = $dir
    }
    elseif (-not ($env:PYTHONPATH -split ';' | Where-Object { $_ -eq $dir })) {
        $env:PYTHONPATH = "$dir;$env:PYTHONPATH"
    }
    if (-not $env:SHOTWRIGHT_DEV_GRACEFUL_SHUTDOWN_SECONDS) {
        $env:SHOTWRIGHT_DEV_GRACEFUL_SHUTDOWN_SECONDS = '120'
    }
    & python -m uvicorn --app-dir $dir app.main:app --host 0.0.0.0 --port 8000 --reload --timeout-graceful-shutdown $env:SHOTWRIGHT_DEV_GRACEFUL_SHUTDOWN_SECONDS
} -ArgumentList "$root\backend"

# Frontend
Write-Host '[dev] Starting frontend (webpack-dev-server) ...' -ForegroundColor Green
$frontendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    $env:SHOTWRIGHT_API_PROXY_TARGET = 'http://127.0.0.1:8000'
    & npm run dev
} -ArgumentList "$root\frontend"

Write-Host ''
Write-Host '  Backend  -> http://localhost:8000/api/docs' -ForegroundColor Cyan
Write-Host '  Frontend -> http://localhost:3000' -ForegroundColor Cyan
Write-Host '  Press Ctrl+C to stop both.' -ForegroundColor Yellow
Write-Host ''

try {
    while ($true) {
        Receive-Job $backendJob -ErrorAction SilentlyContinue
        Receive-Job $frontendJob -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}
finally {
    Write-Host '[dev] Stopping ...' -ForegroundColor Yellow
    Stop-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue
}
