#!/usr/bin/env bash
# Copy the dep-age-gate workflow into one repository.
#
#   scripts/install-workflow.sh <repo-path> [--org <Org>] [--sha <sha>]
#                               [--push-branches "a b c"] [--force]
#
# It renders action/dep-age-gate.yml, replacing the placeholders __ORG__,
# __SHA__ and __PUSH_BRANCHES__.
#
# --org defaults to th3nolo (the public repository). Pass your own org when you
# host a private copy: a private action is only usable by repositories in the
# same org, so each product repo must then reference that org's copy.
#
# --sha defaults to the current HEAD of this tool repository.
# --push-branches defaults to the deploy branches the target repo actually has:
# dev, staging and production when present, plus master or main when that is
# the repository's default branch.
#
# It writes .github/workflows/dep-age-gate.yml and stops there: no `git add`,
# no commit, no push. Review the diff and open a pull request yourself.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$HERE/action/dep-age-gate.yml"

usage() {
    cat <<USAGE
usage: $(basename "$0") <repo-path> [--org <Org>] [--sha <sha>]
                        [--push-branches "a b c"] [--force]

Examples:
  $(basename "$0") ~/src/my-app                      # uses th3nolo/dep-age-gate/action@<HEAD sha>
  $(basename "$0") ~/src/my-app --org MyOrg          # private copy hosted as MyOrg/dep-age-gate
  $(basename "$0") ~/src/my-app --sha <tagged sha>   # pin a specific release commit
USAGE
}

if [ $# -lt 1 ]; then usage; exit 2; fi
REPO="$1"; shift

ORG="th3nolo"
SHA=""
PUSH_BRANCHES=""
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --org) ORG="${2:-}"; shift 2 ;;
        --sha) SHA="${2:-}"; shift 2 ;;
        --push-branches) PUSH_BRANCHES="${2:-}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown argument $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$ORG" ]; then
    echo "error: --org <Org> is required" >&2
    usage >&2
    exit 2
fi
if [ ! -d "$REPO/.git" ]; then
    echo "error: $REPO is not a git repository" >&2
    exit 1
fi
if [ ! -f "$SOURCE" ]; then
    echo "error: workflow template missing at $SOURCE" >&2
    exit 1
fi

if [ -z "$SHA" ]; then
    SHA="$(git -C "$HERE" rev-parse HEAD)"
fi
case "$SHA" in
    *[!0-9a-f]* | "") echo "error: --sha must be a full commit SHA, got '$SHA'" >&2; exit 1 ;;
esac
if [ ${#SHA} -ne 40 ]; then
    echo "error: --sha must be a full 40 character commit SHA, got '$SHA'" >&2
    exit 1
fi

# Deploy branches the target repo actually has. A workflow listing a branch
# that does not exist never runs there, which reads as a passing check.
if [ -z "$PUSH_BRANCHES" ]; then
    HEADS="$(git -C "$REPO" ls-remote --heads origin 2>/dev/null | sed 's#.*refs/heads/##')"
    DEFAULT="$(git -C "$REPO" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
    FOUND=()
    for BRANCH in dev staging production; do
        if printf '%s\n' "$HEADS" | grep -qx "$BRANCH"; then FOUND+=("$BRANCH"); fi
    done
    for BRANCH in master main; do
        if [ "$DEFAULT" = "$BRANCH" ] && printf '%s\n' "$HEADS" | grep -qx "$BRANCH"; then
            FOUND+=("$BRANCH")
        fi
    done
    PUSH_BRANCHES="${FOUND[*]:-}"
fi
if [ -z "$PUSH_BRANCHES" ]; then
    echo "error: no deploy branches found; pass --push-branches \"a b c\"" >&2
    exit 1
fi
# "dev staging production" -> "dev, staging, production"
PUSH_LIST="$(printf '%s\n' $PUSH_BRANCHES | paste -sd, - | sed 's/,/, /g')"

TARGET="$REPO/.github/workflows/dep-age-gate.yml"
RENDERED="$(sed \
    -e "s#__ORG__#$ORG#g" \
    -e "s#__SHA__#$SHA#g" \
    -e "s#__PUSH_BRANCHES__#$PUSH_LIST#g" \
    "$SOURCE")"

if [ -f "$TARGET" ] && [ $FORCE -eq 0 ]; then
    echo "$TARGET already exists; pass --force to overwrite."
    printf '%s\n' "$RENDERED" | diff -u "$TARGET" - || true
    exit 0
fi

mkdir -p "$(dirname "$TARGET")"
printf '%s\n' "$RENDERED" > "$TARGET"
echo "wrote $TARGET"
echo "  action:        $ORG/dep-age-gate/action@$SHA"
echo "  push branches: $PUSH_LIST"
echo
echo "next:"
echo "  1. cd $REPO && git switch -c ci/dep-age-gate"
echo "  2. git add .github/workflows/dep-age-gate.yml"
echo "  3. open a pull request; nothing was committed for you"
