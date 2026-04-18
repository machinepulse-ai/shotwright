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

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$backendStdout = Join-Path $logRoot 'backend.stdout.log'
$backendStderr = Join-Path $logRoot 'backend.stderr.log'
$frontendStdout = Join-Path $logRoot 'frontend.stdout.log'
$frontendStderr = Join-Path $logRoot 'frontend.stderr.log'
$frontendNodeModules = Join-Path $frontendRoot 'node_modules'

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

Write-Host '[dev-container] Starting backend and frontend inside the dev container ...' -ForegroundColor Green

$backendProcess = Start-Process -FilePath 'python' `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--reload') `
    -WorkingDirectory $backendRoot `
    -PassThru `
    -RedirectStandardOutput $backendStdout `
    -RedirectStandardError $backendStderr

if (-not (Test-Path (Join-Path $frontendNodeModules '.bin\webpack.cmd'))) {
    Write-Host '[dev-container] Installing frontend dependencies into node_modules volume ...' -ForegroundColor Yellow
    & 'C:\Program Files\nodejs\npm.cmd' install --no-package-lock --no-progress --fetch-retries 5 --fetch-timeout 120000 --prefix $frontendRoot
    if ($LASTEXITCODE -ne 0) {
        Show-ProcessLogs -Name 'backend' -StdoutPath $backendStdout -StderrPath $backendStderr
        throw 'Failed to install frontend dependencies inside the dev container.'
    }
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