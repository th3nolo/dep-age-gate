# dep-age-gate on Windows

Copy the repository to `%USERPROFILE%\dep-age-gate` (for example with
`git clone https://github.com/th3nolo/dep-age-gate`). The commands below change
`PATH` and your git config, so run them yourself and read each block first.

Open **Windows PowerShell** (not the WSL shell) and run each block.

Checked on a Windows 11 machine on 2026-09-05 (adjust to what you have):

* Python 3.12.10 is on `PATH` as `python` and `py`.
* Git 2.x is at `C:\Program Files\Git\cmd\git.exe`.
* `~\.cargo\bin\cargo.exe` exists but `.cargo\bin` is **not** in
  your user `PATH`. Step 1 puts the wrapper ahead of it anyway, so the ordering
  stays correct if you add `.cargo\bin` later.
* `gradle` and `mvn` are not on `PATH`. The Gradle wrapper is still installed
  for Android Studio projects that call `gradlew.bat`.
* `pnpm` and `yarn` are not on `PATH` on Windows; their config files are in
  place for when they are used.

---

## 1. Put the wrappers on PATH, ahead of `.cargo\bin`

```powershell
$gate = "$env:USERPROFILE\dep-age-gate\wrappers"
$old  = [Environment]::GetEnvironmentVariable("Path", "User")

# Remove any earlier copy of the entry, then prepend it.
$parts = $old -split ";" | Where-Object { $_ -and $_ -ne $gate }
$new   = (@($gate) + $parts) -join ";"

[Environment]::SetEnvironmentVariable("Path", $new, "User")
```

Close and reopen PowerShell, then check:

```powershell
(Get-Command cargo).Source
# expected: $env:USERPROFILE\dep-age-gate\wrappers\cargo.cmd
```

If it prints `...\.cargo\bin\cargo.exe`, the wrapper directory is not first -
re-run the block above and open a new window.

## 2. Make `dep-age-gate` runnable

```powershell
$binDir = "$env:USERPROFILE\bin"        # already on your PATH
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

@'
@echo off
set "PYTHONPATH=%USERPROFILE%\dep-age-gate;%PYTHONPATH%"
python -m dep_age_gate %*
exit /b %ERRORLEVEL%
'@ | Set-Content -Encoding ASCII (Join-Path $binDir "dep-age-gate.cmd")
```

Check:

```powershell
dep-age-gate --version
dep-age-gate check npm:vitest@5.0.0     # must print FAIL and exit 1
```

## 3. Install the pre-commit hook

Git for Windows runs hooks with its own bash, so the same hook file works.

```powershell
$hooks = "$env:USERPROFILE\.config\git\hooks"
New-Item -ItemType Directory -Force -Path $hooks | Out-Null
Copy-Item "$env:USERPROFILE\dep-age-gate\hooks\pre-commit" (Join-Path $hooks "pre-commit") -Force
git config --global core.hooksPath $hooks
git config --global --get core.hooksPath
```

A repository that sets its own `core.hooksPath` (husky does this) overrides the
global one. For each such repo, copy the hook into that directory as well:

```powershell
cd C:\path\to\repo
$local = git config --local --get core.hooksPath
if ($local) {
    Copy-Item "$env:USERPROFILE\dep-age-gate\hooks\pre-commit" (Join-Path $local "pre-commit") -Force
}
```

If a hook is already there, rename it to `pre-commit.local` first - ours runs it
afterwards.

## 4. Config files that are already in place

Verify, do not re-create:

```powershell
Get-Content "$env:USERPROFILE\.npmrc"                                   # min-release-age=3        (days)
Get-Content "$env:LOCALAPPDATA\pnpm\config\config.yaml"                 # minimumReleaseAge: 4320  (minutes)
Get-Content "$env:USERPROFILE\.bunfig.toml"                             # minimumReleaseAge = 259200 (seconds)
Get-Content "$env:USERPROFILE\.yarnrc.yml"                              # npmMinimalAgeGate: "72h"
Get-Content "$env:APPDATA\pip\pip.ini"                                  # uploaded-prior-to = P7D  (ISO-8601)
```

## 5. Optional: Gradle and Maven

The `gradle.cmd` / `mvn.cmd` shims only fire when `gradle` or `mvn` is on
`PATH`. They are not, today. When you install either one, the wrapper directory
is already first, so it will pick it up.

For an Android Studio or Gradle project that uses `gradlew.bat`, PATH cannot
shadow it. Call the wrapper instead:

```powershell
& "$env:USERPROFILE\dep-age-gate\wrappers\gradlew.ps1" build
```

and turn on dependency locking in the project, otherwise there is no lockfile to
check:

```groovy
dependencyLocking { lockAllConfigurations() }
```

```powershell
.\gradlew.bat dependencies --write-locks
```

## 6. Bypass

```powershell
$env:ALLOW_YOUNG_DEPS = "1"     # wrappers and hook warn instead of refusing
Remove-Item Env:\ALLOW_YOUNG_DEPS
```

Do the four-step audit in `README.md` section 3 before you use it.

## 7. Uninstall

```powershell
$gate = "$env:USERPROFILE\dep-age-gate\wrappers"
$old  = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", (($old -split ";" | Where-Object { $_ -and $_ -ne $gate }) -join ";"), "User")
git config --global --unset core.hooksPath
Remove-Item "$env:USERPROFILE\bin\dep-age-gate.cmd"
```
