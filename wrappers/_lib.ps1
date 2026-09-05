# Shared helpers for the Windows dep-age-gate wrappers.
# Dot-source with:  . (Join-Path $PSScriptRoot '_lib.ps1')

$ErrorActionPreference = 'Continue'

function Get-MinAge {
    if ($env:DEP_AGE_GATE_MIN_AGE) { return $env:DEP_AGE_GATE_MIN_AGE }
    return '72h'
}

function Test-Bypass {
    return ($env:ALLOW_YOUNG_DEPS -eq '1')
}

function Get-DepAgeGate {
    # Prefer a dep-age-gate on PATH, then the staged checkout.
    $cmd = Get-Command dep-age-gate -ErrorAction SilentlyContinue
    if ($cmd) { return @{ Kind = 'exe'; Path = $cmd.Source } }
    $repo = Join-Path $env:USERPROFILE 'dep-age-gate'
    if (Test-Path (Join-Path $repo 'dep_age_gate\cli.py')) {
        $py = Get-Command py -ErrorAction SilentlyContinue
        if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
        if ($py) { return @{ Kind = 'python'; Path = $py.Source; Repo = $repo } }
    }
    return $null
}

function Invoke-DepAgeGate {
    param([string[]]$GateArgs)
    $gate = Get-DepAgeGate
    if ($null -eq $gate) { return 127 }
    if ($gate.Kind -eq 'exe') {
        & $gate.Path @GateArgs
        return $LASTEXITCODE
    }
    $saved = $env:PYTHONPATH
    $env:PYTHONPATH = $gate.Repo
    try {
        & $gate.Path -m dep_age_gate @GateArgs
        return $LASTEXITCODE
    } finally {
        $env:PYTHONPATH = $saved
    }
}

function New-LockSnapshot {
    param([string[]]$Paths)
    $snapshots = @{}
    foreach ($path in $Paths) {
        if (Test-Path $path) {
            $temp = [System.IO.Path]::GetTempFileName()
            Copy-Item -LiteralPath $path -Destination $temp -Force
            $snapshots[$path] = $temp
        } else {
            $snapshots[$path] = $null
        }
    }
    return $snapshots
}

function Restore-LockSnapshot {
    param($Snapshots)
    foreach ($path in $Snapshots.Keys) {
        $temp = $Snapshots[$path]
        if ($null -eq $temp) {
            if (Test-Path $path) { Remove-Item -LiteralPath $path -Force }
        } else {
            Copy-Item -LiteralPath $temp -Destination $path -Force
        }
    }
}

function Remove-LockSnapshot {
    param($Snapshots)
    foreach ($temp in $Snapshots.Values) {
        if ($null -ne $temp -and (Test-Path $temp)) { Remove-Item -LiteralPath $temp -Force }
    }
}
