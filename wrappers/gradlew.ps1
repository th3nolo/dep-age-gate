# `gradlew` on Windows resolves to the project's own gradlew.bat, which cannot
# be shadowed by PATH. Call this instead of gradlew.bat, or add
#     . "$env:USERPROFILE\dep-age-gate\wrappers\gradlew.ps1" <args>
# to your build scripts. It applies the same lockfile guard as gradle.ps1.

. (Join-Path $PSScriptRoot '_lib.ps1')

$wrapper = Join-Path (Get-Location) 'gradlew.bat'
if (-not (Test-Path $wrapper)) {
    Write-Error "gradlew wrapper: no gradlew.bat in $(Get-Location)"
    exit 127
}
$env:DEP_AGE_GATE_REAL_GRADLE = $wrapper
& (Join-Path $PSScriptRoot 'gradle.ps1') @args
exit $LASTEXITCODE
