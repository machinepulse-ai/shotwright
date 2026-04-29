$ErrorActionPreference = 'Stop'

$backendRoot = 'C:\workspace\src\backend'
$frontendRoot = 'C:\workspace\src\frontend'
$logRoot = 'C:\workspace\logs'

function Show-ProcessLogs {
    param(
        [string]$Name,
        [string]$StdoutPath,
        [string]$StderrPath
    )

    if (Test-Path $StdoutPath) {
        $stdout = Get-Content -Path $StdoutPath -Tail 200 -ErrorAction SilentlyContinue
        if ($stdout) {
            Write-Host "[dev-container] $Name stdout:" -ForegroundColor DarkCyan
            $stdout | ForEach-Object { Write-Host $_ }
        }
    }

    if (Test-Path $StderrPath) {
        $stderr = Get-Content -Path $StderrPath -Tail 200 -ErrorAction SilentlyContinue
        if ($stderr) {
            Write-Host "[dev-container] $Name stderr:" -ForegroundColor DarkYellow
            $stderr | ForEach-Object { Write-Host $_ }
        }
    }
}

function Get-FrontendDependencyFingerprint {
    param(
        [string[]]$Paths
    )

    $hashes = foreach ($path in $Paths) {
        if (Test-Path $path) {
            (Get-FileHash -Algorithm SHA256 -Path $path).Hash
        }
        else {
            "missing:$path"
        }
    }

    return ($hashes -join '|')
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$backendStdout = Join-Path $logRoot 'backend.stdout.log'
$backendStderr = Join-Path $logRoot 'backend.stderr.log'
$frontendStdout = Join-Path $logRoot 'frontend.stdout.log'
$frontendStderr = Join-Path $logRoot 'frontend.stderr.log'
$frontendPackageJson = Join-Path $frontendRoot 'package.json'
$frontendPackageLock = Join-Path $frontendRoot 'package-lock.json'
$frontendNodeModules = Join-Path $frontendRoot 'node_modules'
$frontendDependencyStamp = Join-Path $frontendNodeModules '.shotwright-deps.sha256'

@($backendStdout, $backendStderr, $frontendStdout, $frontendStderr) | ForEach-Object {
    if (Test-Path $_) {
        Remove-Item $_ -Force
    }
}

if (-not $env:SHOTWRIGHT_MONGO_URI) {
    $env:SHOTWRIGHT_MONGO_URI = 'mongodb://mongo:27017'
}

if (-not $env:SHOTWRIGHT_API_PROXY_TARGET) {
    $env:SHOTWRIGHT_API_PROXY_TARGET = 'http://127.0.0.1:8000'
}

$env:SHOTWRIGHT_DEBUG = 'true'
if (-not $env:WATCHFILES_FORCE_POLLING) {
    $env:WATCHFILES_FORCE_POLLING = '1'
}
if (-not $env:WATCHFILES_POLL_DELAY_MS) {
    $env:WATCHFILES_POLL_DELAY_MS = '500'
}
if (-not $env:PYTHONPATH) {
    $env:PYTHONPATH = $backendRoot
}
elseif (-not ($env:PYTHONPATH -split ';' | Where-Object { $_ -eq $backendRoot })) {
    $env:PYTHONPATH = "$backendRoot;$env:PYTHONPATH"
}
if (-not $env:SHOTWRIGHT_DEV_GRACEFUL_SHUTDOWN_SECONDS) {
    $env:SHOTWRIGHT_DEV_GRACEFUL_SHUTDOWN_SECONDS = '12'
}

$gracefulShutdownSeconds = $env:SHOTWRIGHT_DEV_GRACEFUL_SHUTDOWN_SECONDS
$verifySkillsSsl = $true
if ($env:SHOTWRIGHT_SKILLS_VERIFY_SSL) {
    $normalizedSkillsVerifySsl = $env:SHOTWRIGHT_SKILLS_VERIFY_SSL.Trim().ToLowerInvariant()
    if (@('0', 'false', 'no', 'off') -contains $normalizedSkillsVerifySsl) {
        $verifySkillsSsl = $false
    }
}

$skillsDownloadArgs = @(
    'C:\workspace\scripts\skills\download_skills_bundle.py',
    '--install-root', 'C:\workspace',
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

Write-Host '[dev-container] Hydrating repo skills into .github/skills ...' -ForegroundColor Green
& python @skillsDownloadArgs
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to hydrate .github/skills before starting dev processes.'
}

Write-Host '[dev-container] Starting backend and frontend inside the dev container ...' -ForegroundColor Green

$backendProcess = Start-Process -FilePath 'python' `
    -ArgumentList @('-m', 'uvicorn', '--app-dir', $backendRoot, 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--reload', '--timeout-graceful-shutdown', $gracefulShutdownSeconds) `
    -WorkingDirectory $backendRoot `
    -PassThru `
    -RedirectStandardOutput $backendStdout `
    -RedirectStandardError $backendStderr

if (-not (Test-Path (Join-Path $frontendNodeModules '.bin\webpack.cmd'))) {
    $frontendDependenciesNeedSync = $true
}
else {
    $expectedDependencyFingerprint = Get-FrontendDependencyFingerprint -Paths @($frontendPackageJson, $frontendPackageLock)
    $cachedDependencyFingerprint = if (Test-Path $frontendDependencyStamp) {
        $cachedDependencyFingerprintContent = Get-Content -Path $frontendDependencyStamp -Raw -ErrorAction SilentlyContinue
        if ($null -ne $cachedDependencyFingerprintContent) {
            $cachedDependencyFingerprintContent.Trim()
        }
        else {
            ''
        }
    }
    else {
        ''
    }
    $frontendDependenciesNeedSync = $expectedDependencyFingerprint -ne $cachedDependencyFingerprint
}

if ($frontendDependenciesNeedSync) {
    Write-Host '[dev-container] Syncing frontend dependencies into the node_modules volume ...' -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $frontendNodeModules | Out-Null
    & 'C:\Program Files\nodejs\npm.cmd' install --no-progress --fetch-retries 5 --fetch-timeout 120000 --prefix $frontendRoot
    if ($LASTEXITCODE -ne 0) {
        Show-ProcessLogs -Name 'backend' -StdoutPath $backendStdout -StderrPath $backendStderr
        throw 'Failed to install frontend dependencies inside the dev container.'
    }

    $currentDependencyFingerprint = Get-FrontendDependencyFingerprint -Paths @($frontendPackageJson, $frontendPackageLock)
    Set-Content -Path $frontendDependencyStamp -Value $currentDependencyFingerprint -NoNewline
}

$frontendProcess = Start-Process -FilePath 'C:\Program Files\nodejs\npm.cmd' `
    -ArgumentList @('run', 'dev') `
    -WorkingDirectory $frontendRoot `
    -PassThru `
    -RedirectStandardOutput $frontendStdout `
    -RedirectStandardError $frontendStderr

$processes = @(
    @{ Name = 'backend'; Process = $backendProcess; Stdout = $backendStdout; Stderr = $backendStderr },
    @{ Name = 'frontend'; Process = $frontendProcess; Stdout = $frontendStdout; Stderr = $frontendStderr }
)

Write-Host '  Backend  -> http://localhost:8000/api/docs' -ForegroundColor Cyan
Write-Host '  Frontend -> http://localhost:3000' -ForegroundColor Cyan
Write-Host "  Logs      -> $logRoot" -ForegroundColor DarkGray

try {
    while ($true) {
        foreach ($entry in $processes) {
            $process = $entry.Process
            if ($process.HasExited) {
                Show-ProcessLogs -Name $entry.Name -StdoutPath $entry.Stdout -StderrPath $entry.Stderr
                throw "The $($entry.Name) process exited with code $($process.ExitCode)."
            }
        }

        Start-Sleep -Seconds 2
    }
}
finally {
    Write-Host '[dev-container] Stopping child processes ...' -ForegroundColor Yellow
    foreach ($entry in $processes) {
        if ($entry.Process -and -not $entry.Process.HasExited) {
            Stop-Process -Id $entry.Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
