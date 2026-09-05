# Supply-chain protection wrapper for `mvn` on Windows.
#
# Maven writes no lockfile, so there is no "after" state to diff. This wrapper
# audits pom.xml BEFORE the build and refuses to run when a direct dependency
# with an explicit version is younger than the minimum age.
#
# What it does NOT cover: transitive dependencies and versions inherited from a
# parent POM or BOM. Add dependency locking or a pinned bill of materials if you
# need those covered.
#
# Bypass:  $env:ALLOW_YOUNG_DEPS=1 ; mvn package

. (Join-Path $PSScriptRoot '_lib.ps1')

$realMvn = $env:DEP_AGE_GATE_REAL_MVN
if (-not $realMvn) {
    $candidate = Get-Command mvn.cmd -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike (Join-Path $PSScriptRoot '*') } |
        Select-Object -First 1
    if ($candidate) { $realMvn = $candidate.Source }
}
if (-not $realMvn -or -not (Test-Path $realMvn)) {
    Write-Error "mvn wrapper: real mvn not found. Set DEP_AGE_GATE_REAL_MVN."
    exit 127
}

$minAge = Get-MinAge
$pom = Join-Path (Get-Location) 'pom.xml'

if ((Test-Path $pom) -and -not (Test-Bypass)) {
    $rc = Invoke-DepAgeGate -GateArgs @('audit', '--lock', $pom, '--all', '--min-age', $minAge)
    if ($rc -eq 127) {
        Write-Error "mvn wrapper: dep-age-gate not found. Failing closed."
        exit 1
    }
    if ($rc -ne 0) {
        Write-Host ""
        Write-Error "mvn wrapper: refused - pom.xml declares dependency versions younger than $minAge."
        Write-Host "  Check one:            dep-age-gate check maven:<group>:<artifact>:<version>"
        Write-Host "  Bypass after audit:   `$env:ALLOW_YOUNG_DEPS=1"
        exit 1
    }
} elseif (Test-Bypass) {
    Write-Warning "ALLOW_YOUNG_DEPS=1 - pom.xml was NOT age-checked."
}

& $realMvn @args
exit $LASTEXITCODE
