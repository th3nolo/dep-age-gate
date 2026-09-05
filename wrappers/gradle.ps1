# Supply-chain protection wrapper for `gradle` on Windows.
#
# Gradle has no release-age gate. This wrapper snapshots every gradle.lockfile
# in the project, runs the real gradle, then audits any newly locked coordinate
# and rolls the lockfiles back when one is too young.
#
# REQUIREMENT: the project must have dependency locking turned on, otherwise
# there is no gradle.lockfile to audit. See README.md for the build.gradle
# snippet.
#
# Bypass:  $env:ALLOW_YOUNG_DEPS=1 ; gradle build

. (Join-Path $PSScriptRoot '_lib.ps1')

$realGradle = $env:DEP_AGE_GATE_REAL_GRADLE
if (-not $realGradle) {
    $candidate = Get-Command gradle.bat -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike (Join-Path $PSScriptRoot '*') } |
        Select-Object -First 1
    if ($candidate) { $realGradle = $candidate.Source }
}
if (-not $realGradle -or -not (Test-Path $realGradle)) {
    Write-Error "gradle wrapper: real gradle not found. Set DEP_AGE_GATE_REAL_GRADLE."
    exit 127
}

$minAge = Get-MinAge
$locks = @(Get-ChildItem -Recurse -Filter 'gradle.lockfile' -File -ErrorAction SilentlyContinue |
    ForEach-Object { $_.FullName })
$snapshots = New-LockSnapshot $locks

& $realGradle @args
$gradleExit = $LASTEXITCODE

try {
    $after = @(Get-ChildItem -Recurse -Filter 'gradle.lockfile' -File -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName })
    if ($after.Count -eq 0) {
        if ($locks.Count -eq 0) {
            Write-Host "gradle wrapper: no gradle.lockfile found - dependency locking is OFF, nothing audited." -ForegroundColor Yellow
            Write-Host "  Turn it on:  dependencyLocking { lockAllConfigurations() }   then  gradle dependencies --write-locks"
        }
        exit $gradleExit
    }

    $changed = @()
    foreach ($lock in $after) {
        $before = $null
        if ($snapshots.ContainsKey($lock)) { $before = $snapshots[$lock] }
        if (-not $before) { $changed += ,@($lock, $null); continue }
        $a = (Get-FileHash -LiteralPath $before -Algorithm SHA256).Hash
        $b = (Get-FileHash -LiteralPath $lock -Algorithm SHA256).Hash
        if ($a -ne $b) { $changed += ,@($lock, $before) }
    }
    if ($changed.Count -eq 0) { exit $gradleExit }

    if (Test-Bypass) {
        Write-Warning "ALLOW_YOUNG_DEPS=1 - gradle.lockfile changed and was NOT age-checked."
        exit $gradleExit
    }

    $violated = $false
    foreach ($pair in $changed) {
        $gateArgs = @('audit', '--lock', $pair[0], '--min-age', $minAge)
        if ($pair[1]) { $gateArgs += @('--before', $pair[1]) } else { $gateArgs += '--all' }
        $rc = Invoke-DepAgeGate -GateArgs $gateArgs
        if ($rc -eq 127) {
            Write-Error "gradle wrapper: dep-age-gate not found. Failing closed."
            $violated = $true; break
        }
        if ($rc -ne 0) { $violated = $true }
    }

    if ($violated) {
        Restore-LockSnapshot $snapshots
        Write-Host ""
        Write-Error "gradle wrapper: refused - lockfile added coordinates younger than $minAge. Lockfiles restored."
        Write-Host "  Check one:            dep-age-gate check maven:<group>:<artifact>:<version>"
        Write-Host "  Bypass after audit:   `$env:ALLOW_YOUNG_DEPS=1"
        exit 1
    }
    exit $gradleExit
} finally {
    Remove-LockSnapshot $snapshots
}
