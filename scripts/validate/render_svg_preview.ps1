param(
    [Parameter(Mandatory)]
    [string]$SvgPath,

    [string]$OutputPath = '',

    [string]$BrowserPath = '',

    [int]$Width = 1200,

    [int]$Height = 860
)

$ErrorActionPreference = 'Stop'

function Resolve-BrowserPath {
    param([string]$RequestedPath)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        if (-not (Test-Path $RequestedPath)) {
            throw "Browser executable not found at $RequestedPath"
        }
        return $RequestedPath
    }

    $candidates = @(
        'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        'C:\Program Files\Google\Chrome\Application\chrome.exe',
        'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'
    )

    $browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($null -eq $browser) {
        throw 'No supported browser was found. Install Microsoft Edge or Google Chrome, or pass -BrowserPath explicitly.'
    }

    return $browser
}

$resolvedSvg = (Resolve-Path -Path $SvgPath).Path
$resolvedBrowser = Resolve-BrowserPath -RequestedPath $BrowserPath

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = [System.IO.Path]::ChangeExtension($resolvedSvg, '.png')
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $resolvedOutput
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}

if (Test-Path $resolvedOutput) {
    Remove-Item $resolvedOutput -Force
}

$svgUri = ([System.Uri]$resolvedSvg).AbsoluteUri
$arguments = @(
    '--headless',
    '--disable-gpu',
    '--hide-scrollbars',
    "--window-size=$Width,$Height",
    "--screenshot=$resolvedOutput",
    $svgUri
)

$process = Start-Process -FilePath $resolvedBrowser -ArgumentList $arguments -Wait -PassThru -NoNewWindow
if ($process.ExitCode -ne 0) {
    throw "Browser renderer exited with code $($process.ExitCode)."
}

if (-not (Test-Path $resolvedOutput)) {
    throw "PNG output was not produced at $resolvedOutput"
}

Add-Type -AssemblyName System.Drawing
$image = [System.Drawing.Image]::FromFile($resolvedOutput)
try {
    if ($image.Width -ne $Width -or $image.Height -ne $Height) {
        throw "PNG output size mismatch: expected ${Width}x${Height}, got $($image.Width)x$($image.Height)."
    }

    [PSCustomObject]@{
        SvgPath = $resolvedSvg
        OutputPath = $resolvedOutput
        Width = $image.Width
        Height = $image.Height
        BrowserPath = $resolvedBrowser
    } | Format-List | Out-String | Write-Host
}
finally {
    $image.Dispose()
}