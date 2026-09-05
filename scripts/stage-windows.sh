#!/usr/bin/env bash
# Copy everything Windows needs to %USERPROFILE%\dep-age-gate\ from WSL.
# It only copies files. It does not touch the Windows PATH, the registry, or
# any Windows git config - the user runs INSTALL-WINDOWS.md by hand.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:?usage: scripts/stage-windows.sh /mnt/c/Users/<windows-user>/dep-age-gate}"

mkdir -p "$DEST"
for item in dep_age_gate wrappers hooks action scripts tests .dep-age-gate-ignore README.md LICENSE pyproject.toml INSTALL-WINDOWS.md; do
    if [ -e "$HERE/$item" ]; then
        rm -rf "${DEST:?}/$item"
        cp -r "$HERE/$item" "$DEST/$item"
    fi
done
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "staged to $DEST"
find "$DEST" -maxdepth 2 -type f | sed "s|$DEST|%USERPROFILE%\\\\dep-age-gate|" | sed 's|/|\\|g' | sort
