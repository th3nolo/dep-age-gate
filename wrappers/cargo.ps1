# Supply-chain protection wrapper for `cargo` on Windows.
#
# cargo has no built-in release-age gate (-Z min-publish-age is nightly only).
# This wrapper snapshots Cargo.lock, runs the real cargo, then audits any new
# crate version and rolls Cargo.lock back when one is too young.
#
# Bypass:  $env:ALLOW_YOUNG_DEPS=1 ; cargo add <crate>

. (Join-Path $PSScriptRoot '_lib.ps1')

$realCargo = $env:DEP_AGE_GATE_REAL_CARGO
if (-not $realCargo) { $realCargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe' }
if (-not (Test-Path $realCargo)) {
    Write-Error "cargo wrapper: real cargo not found at $realCargo"
    exit 127
}

$minAge = Get-MinAge

$manifest = & $realCargo locate-project --workspace --message-format plain 2>$null
if ($LASTEXITCODE -ne 0 -or -not $manifest) {
    & $realCargo @args
    exit $LASTEXITCODE
}

$lockfile = Join-Path (Split-Path -Parent $manifest) 'Cargo.lock'

# `cargo add` / `remove` / `upgrade` edit Cargo.toml too. Snapshot it for those
# commands only, so a refusal leaves no half-applied dependency behind.
$firstVerb = ($args | Where-Object { $_ -notlike '-*' } | Select-Object -First 1)
$guardManifest = @('add', 'remove', 'rm', 'upgrade') -contains $firstVerb

$toSnapshot = @($lockfile)
if ($guardManifest) { $toSnapshot += $manifest }
$snapshots = New-LockSnapshot $toSnapshot
$before = $snapshots[$lockfile]

& $realCargo @args
$cargoExit = $LASTEXITCODE

try {
    if (-not (Test-Path $lockfile)) { exit $cargoExit }

    if ($before) {
        $a = (Get-FileHash -LiteralPath $before -Algorithm SHA256).Hash
        $b = (Get-FileHash -LiteralPath $lockfile -Algorithm SHA256).Hash
        if ($a -eq $b) { exit $cargoExit }
    }

    if (Test-Bypass) {
        Write-Warning "ALLOW_YOUNG_DEPS=1 - Cargo.lock changed and was NOT age-checked."
        Write-Warning "Audit before committing: dep-age-gate audit --lock `"$lockfile`" --all"
        exit $cargoExit
    }

    $gateArgs = @('audit', '--lock', $lockfile, '--min-age', $minAge)
    if ($before) { $gateArgs += @('--before', $before) } else { $gateArgs += '--all' }

    $rc = Invoke-DepAgeGate -GateArgs $gateArgs
    if ($rc -eq 127) {
        Write-Error "cargo wrapper: Cargo.lock changed but dep-age-gate was not found. Failing closed."
        Restore-LockSnapshot $snapshots
        exit 1
    }
    if ($rc -ne 0) {
        Write-Host ""
        Write-Error "cargo wrapper: refused - Cargo.lock added crate versions younger than $minAge."
        Restore-LockSnapshot $snapshots
        if ($guardManifest) {
            Write-Host "  Cargo.lock and Cargo.toml restored." -ForegroundColor Yellow
        } else {
            Write-Host "  Cargo.lock restored." -ForegroundColor Yellow
        }
        Write-Host "  Pin an older version:  cargo add <crate>@<older-version>"
        Write-Host "  Check one version:     dep-age-gate check crates:<crate>@<version>"
        Write-Host "  Bypass after audit:    `$env:ALLOW_YOUNG_DEPS=1"
        exit 1
    }
    exit $cargoExit
} finally {
    Remove-LockSnapshot $snapshots
}
