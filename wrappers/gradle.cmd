@echo off
REM dep-age-gate wrapper shim. Keep this file next to gradle.ps1 and put its
REM directory BEFORE the real tool's directory on PATH.
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0gradle.ps1" %*
exit /b %ERRORLEVEL%
