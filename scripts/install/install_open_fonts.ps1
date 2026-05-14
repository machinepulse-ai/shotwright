param(
    [string]$DownloadRoot = 'C:\ProgramData\Shotwright\fonts',
    [string]$WindowsFontsRoot = "$env:WINDIR\Fonts",
    [switch]$SkipDownload
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$FontRegistryPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts'

$Packages = @(
    @{
        Name = 'Noto Sans SC'
        License = 'SIL Open Font License 1.1'
        Source = 'https://github.com/notofonts/noto-cjk/releases/tag/Sans2.004'
        Url = 'https://github.com/notofonts/noto-cjk/releases/download/Sans2.004/18_NotoSansSC.zip'
        ArchiveName = '18_NotoSansSC.zip'
        RecommendedPostScriptNames = @(
            'NotoSansSC-Regular',
            'NotoSansSC-Medium',
            'NotoSansSC-Bold',
            'NotoSansSC-Black'
        )
    },
    @{
        Name = 'Noto Serif SC'
        License = 'SIL Open Font License 1.1'
        Source = 'https://github.com/notofonts/noto-cjk/releases/tag/Serif2.003'
        Url = 'https://github.com/notofonts/noto-cjk/releases/download/Serif2.003/14_NotoSerifSC.zip'
        ArchiveName = '14_NotoSerifSC.zip'
        RecommendedPostScriptNames = @(
            'NotoSerifSC-Regular',
            'NotoSerifSC-Medium',
            'NotoSerifSC-Bold',
            'NotoSerifSC-Black'
        )
    },
    @{
        Name = 'LXGW WenKai Regular'
        License = 'SIL Open Font License 1.1'
        Source = 'https://github.com/lxgw/LxgwWenKai/releases/tag/v1.522'
        Url = 'https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Regular.ttf'
        ArchiveName = 'LXGWWenKai-Regular.ttf'
        RecommendedPostScriptNames = @('LXGWWenKai-Regular')
    },
    @{
        Name = 'LXGW WenKai Medium'
        License = 'SIL Open Font License 1.1'
        Source = 'https://github.com/lxgw/LxgwWenKai/releases/tag/v1.522'
        Url = 'https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Medium.ttf'
        ArchiveName = 'LXGWWenKai-Medium.ttf'
        RecommendedPostScriptNames = @('LXGWWenKai-Medium')
    }
)

function Invoke-FontDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if ((Test-Path -LiteralPath $Destination) -and ((Get-Item -LiteralPath $Destination).Length -gt 0)) {
        Write-Host "Using cached font package $Destination"
        return
    }

    $destinationDirectory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
    $temporaryPath = "$Destination.tmp"
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }

    Write-Host "Downloading $Url"
    & curl.exe -L --fail --silent --show-error --retry 5 --retry-delay 2 --connect-timeout 30 --output $temporaryPath $Url
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download font package from $Url"
    }
    Move-Item -LiteralPath $temporaryPath -Destination $Destination -Force
}

function Expand-FontPackage {
    param(
        [Parameter(Mandatory = $true)][string]$PackagePath,
        [Parameter(Mandatory = $true)][string]$ExtractRoot
    )

    $extension = [System.IO.Path]::GetExtension($PackagePath).ToLowerInvariant()
    if ($extension -in @('.ttf', '.otf', '.ttc')) {
        return @((Get-Item -LiteralPath $PackagePath))
    }

    $packageStem = [System.IO.Path]::GetFileNameWithoutExtension($PackagePath)
    $extractPath = Join-Path $ExtractRoot $packageStem
    if (Test-Path -LiteralPath $extractPath) {
        Remove-Item -LiteralPath $extractPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null
    Expand-Archive -LiteralPath $PackagePath -DestinationPath $extractPath -Force
    return @(Get-ChildItem -LiteralPath $extractPath -Recurse -File | Where-Object { $_.Extension -match '^\.(otf|ttf|ttc)$' })
}

function Install-FontFile {
    param([Parameter(Mandatory = $true)][System.IO.FileInfo]$FontFile)

    New-Item -ItemType Directory -Path $WindowsFontsRoot -Force | Out-Null
    $targetPath = Join-Path $WindowsFontsRoot $FontFile.Name
    if (-not (Test-Path -LiteralPath $targetPath) -or ((Get-Item -LiteralPath $targetPath).Length -ne $FontFile.Length)) {
        Copy-Item -LiteralPath $FontFile.FullName -Destination $targetPath -Force
    }

    $fontKind = switch ($FontFile.Extension.ToLowerInvariant()) {
        '.otf' { 'OpenType' }
        '.ttc' { 'TrueType Collection' }
        default { 'TrueType' }
    }
    $registryName = "$($FontFile.BaseName) ($fontKind)"
    New-ItemProperty -Path $FontRegistryPath -Name $registryName -Value $FontFile.Name -PropertyType String -Force | Out-Null

    return [ordered]@{
        file = $FontFile.Name
        installed_path = $targetPath
        registry_name = $registryName
    }
}

New-Item -ItemType Directory -Path $DownloadRoot -Force | Out-Null
New-Item -ItemType Directory -Path $WindowsFontsRoot -Force | Out-Null

$installedFonts = @()
$manifestPackages = @()
foreach ($package in $Packages) {
    $packagePath = Join-Path $DownloadRoot $package.ArchiveName
    if (-not $SkipDownload) {
        Invoke-FontDownload -Url $package.Url -Destination $packagePath
    }
    elseif (-not (Test-Path -LiteralPath $packagePath)) {
        Write-Warning "Skipping missing font package $packagePath because -SkipDownload was set."
        continue
    }

    $fontFiles = Expand-FontPackage -PackagePath $packagePath -ExtractRoot (Join-Path $DownloadRoot 'expanded')
    foreach ($fontFile in $fontFiles) {
        $installedFonts += Install-FontFile -FontFile $fontFile
    }
    $manifestPackages += [ordered]@{
        name = $package.Name
        license = $package.License
        source = $package.Source
        url = $package.Url
        recommended_postscript_names = $package.RecommendedPostScriptNames
    }
}

$manifest = [ordered]@{
    installed_at = (Get-Date).ToUniversalTime().ToString('o')
    packages = $manifestPackages
    installed_fonts = $installedFonts
}
$manifestPath = Join-Path $DownloadRoot 'shotwright-fonts.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "Installed $($installedFonts.Count) Shotwright open font files. Manifest: $manifestPath"
