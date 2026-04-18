param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [string]$OutputPath = "",

    [double]$IntervalSeconds = 1.0,

    [int]$Columns = 4,

    [int]$Width = 320
)

if (-not (Test-Path $InputPath)) {
    throw "Input video not found: $InputPath"
}

if (-not $OutputPath) {
    $directory = Split-Path -Path $InputPath -Parent
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($InputPath)
    $OutputPath = Join-Path $directory "$stem.storyboard.jpg"
}

$interval = [Math]::Max($IntervalSeconds, 0.1)
$columns = [Math]::Max($Columns, 1)
$width = [Math]::Max($Width, 64)

$durationRaw = & ffprobe -v error -show_entries format^=duration -of default^=noprint_wrappers^=1:nokey^=1 $InputPath
if ($LASTEXITCODE -ne 0) {
    throw "ffprobe failed for $InputPath"
}

$duration = [double]::Parse($durationRaw.Trim(), [System.Globalization.CultureInfo]::InvariantCulture)
$frameCount = [Math]::Max([int][Math]::Ceiling($duration / $interval), 1)
$rows = [Math]::Max([int][Math]::Ceiling($frameCount / $columns), 1)
$filter = "fps=1/$interval,scale=$width:-1,tile=${columns}x${rows}:margin=8:padding=8:color=white"

& ffmpeg -y -i $InputPath -vf $filter -frames:v 1 $OutputPath
if ($LASTEXITCODE -ne 0) {
    throw "ffmpeg failed while generating storyboard for $InputPath"
}

[pscustomobject]@{
    input = $InputPath
    output = $OutputPath
    intervalSeconds = $interval
    columns = $columns
    rows = $rows
    width = $width
    estimatedFrames = $frameCount
} | ConvertTo-Json -Compress