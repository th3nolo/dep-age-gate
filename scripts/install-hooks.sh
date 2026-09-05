#!/usr/bin/env bash
# Install the dep-age-gate pre-commit hook.
#
#   scripts/install-hooks.sh                 # global baseline only
#   scripts/install-hooks.sh <repo> [<repo>] # plus repos that override hooksPath
#
# Two layers, because a repo-local core.hooksPath beats the global one:
#   1. global core.hooksPath -> ~/.config/git/hooks   (every repo with no override)
#   2. per repo, when that repo sets its own core.hooksPath (husky and friends)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_SOURCE="$HERE/hooks/pre-commit"
GLOBAL_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/git/hooks"

# Never overwrite a file that git tracks. A tracked hook belongs to the
# repository, not to this machine: replacing it shows up as an uncommitted
# change in someone else's project.
is_tracked() {
    local repo="$1" file="$2"
    git -C "$repo" ls-files --error-unmatch "$file" >/dev/null 2>&1
}

install_into() {
    local dir="$1" repo="${2:-}"
    mkdir -p "$dir"
    local target="$dir/pre-commit"

    if [ -n "$repo" ] && [ -e "$target" ] && is_tracked "$repo" "$target"; then
        echo "  SKIPPED: $target is tracked by git."
        echo "           Add this line to it and commit the change yourself:"
        echo "             command -v dep-age-gate >/dev/null && dep-age-gate audit || exit 1"
        echo "           Until then this repo is covered by CI only."
        return 0
    fi

    if [ -e "$target" ] && ! grep -q "dep-age-gate pre-commit hook" "$target" 2>/dev/null; then
        mv "$target" "$dir/pre-commit.local"
        chmod +x "$dir/pre-commit.local"
        echo "  moved existing hook aside -> $dir/pre-commit.local (it still runs, after ours)"
    fi
    cp "$HOOK_SOURCE" "$target"
    chmod +x "$target"
    echo "  installed $target"
}

echo "global baseline:"
install_into "$GLOBAL_DIR"
git config --global core.hooksPath "$GLOBAL_DIR"
echo "  git config --global core.hooksPath = $GLOBAL_DIR"

for repo in "$@"; do
    if [ ! -d "$repo/.git" ] && [ ! -f "$repo/.git" ]; then
        echo "skip $repo: not a git repository"
        continue
    fi
    local_path="$(git -C "$repo" config --local --get core.hooksPath || true)"
    echo "$repo:"
    if [ -z "$local_path" ]; then
        echo "  no local core.hooksPath; the global hook covers it"
        continue
    fi
    case "$local_path" in
        /*) dir="$local_path" ;;
        *)  dir="$repo/$local_path" ;;
    esac
    echo "  local core.hooksPath = $local_path"
    install_into "$dir" "$repo"
done
