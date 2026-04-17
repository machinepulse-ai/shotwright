$ErrorActionPreference = 'Stop'

function Test-FlagEnabled {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }

    switch ($Value.Trim().ToLowerInvariant()) {
        '0' { return $false }
        'false' { return $false }
        'no' { return $false }
        default { return $true }
    }
}

$autoInstallValue = if (-not [string]::IsNullOrWhiteSpace($env:SHOTWRIGHT_AUTO_INSTALL_AFTER_EFFECTS)) {
    $env:SHOTWRIGHT_AUTO_INSTALL_AFTER_EFFECTS
} else {
    $env:AUTO_INSTALL_AFTER_EFFECTS
}

if (Test-FlagEnabled $autoInstallValue) {
    $payloadRoot = if (-not [string]::IsNullOrWhiteSpace($env:SHOTWRIGHT_INSTALLER_PAYLOAD_ROOT)) {
        $env:SHOTWRIGHT_INSTALLER_PAYLOAD_ROOT
    } else {
        'C:\lab\payload'
    }

    & 'C:\workspace\scripts\install_after_effects_in_container.ps1' -InstallerPayloadRoot $payloadRoot
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& 'C:\workspace\keepalive.ps1'