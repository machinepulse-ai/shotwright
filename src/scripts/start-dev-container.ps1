$ErrorActionPreference = 'Stop'

$backendRoot = 'C:\workspace\src\backend'
$frontendRoot = 'C:\workspace\src\frontend'

Write-Host '[dev-container] Starting backend and frontend inside the dev container ...' -ForegroundColor Green

$backendJob = Start-Job -Name backend -ScriptBlock {
    param($root)

    $ErrorActionPreference = 'Stop'
    Set-Location $root

    if (-not $env:SHOTWRIGHT_MONGO_URI) {
        $env:SHOTWRIGHT_MONGO_URI = 'mongodb://mongo:27017'
    }

    $env:SHOTWRIGHT_DEBUG = 'true'
    & python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
} -ArgumentList $backendRoot

$frontendJob = Start-Job -Name frontend -ScriptBlock {
    param($root)

    $ErrorActionPreference = 'Stop'
    Set-Location $root

    if (-not $env:SHOTWRIGHT_API_PROXY_TARGET) {
        $env:SHOTWRIGHT_API_PROXY_TARGET = 'http://127.0.0.1:8000'
    }

    & 'C:\Program Files\nodejs\npm.cmd' run dev
} -ArgumentList $frontendRoot

$jobs = @($backendJob, $frontendJob)

Write-Host '  Backend  -> http://localhost:8000/api/docs' -ForegroundColor Cyan
Write-Host '  Frontend -> http://localhost:3000' -ForegroundColor Cyan

try {
    while ($true) {
        foreach ($job in $jobs) {
            Receive-Job -Job $job -Keep -ErrorAction SilentlyContinue

            if ($job.State -in @('Completed', 'Failed', 'Stopped')) {
                Receive-Job -Job $job -Keep -ErrorAction SilentlyContinue
                throw "The $($job.Name) process exited with state $($job.State)."
            }
        }

        Start-Sleep -Seconds 2
    }
}
finally {
    Write-Host '[dev-container] Stopping background jobs ...' -ForegroundColor Yellow
    Stop-Job -Job $jobs -ErrorAction SilentlyContinue
    Remove-Job -Job $jobs -Force -ErrorAction SilentlyContinue
}