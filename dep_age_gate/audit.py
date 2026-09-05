"""Lockfile audit: find every dependency version that is new or changed
relative to a base, then check each one's publish date.

Base selection, in order of precedence:

  --before FILE  paired with --lock FILE   (used by the cargo/gradle wrappers)
  --base REF     git ref, compared against the working tree
  (default)      HEAD compared against the staging area  (pre-commit hook)
  --all          no base at all: every version in the file is checked
"""

import fnmatch
import os

from dep_age_gate import gitutil
from dep_age_gate.model import ParseOutcome
from dep_age_gate.parsers import is_supported, parse, parser_for

BINARY_NAMES = ("bun.lockb",)
IGNORE_FILE = ".dep-age-gate-ignore"


def load_ignores(root=None, extra=None):
    """Globs of lockfiles to skip.

    Read from `.dep-age-gate-ignore` in the repository root - one glob per
    line, `#` starts a comment. Use it for lockfiles that are test data rather
    than dependencies this project installs.
    """
    patterns = list(extra or [])
    if root:
        path = os.path.join(root, IGNORE_FILE)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        patterns.append(line)
        except OSError:
            pass
    return patterns


def is_ignored(label, patterns, root=None):
    if not patterns:
        return False
    candidates = [label.replace(os.sep, "/")]
    absolute = os.path.abspath(label)
    candidates.append(absolute.replace(os.sep, "/"))
    if root:
        try:
            candidates.append(
                os.path.relpath(absolute, root).replace(os.sep, "/")
            )
        except ValueError:
            pass
    for pattern in patterns:
        normalized = pattern.replace(os.sep, "/")
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, normalized):
                return True
            # `tests/fixtures/**` should also match `tests/fixtures/uv.lock`
            if normalized.endswith("/**") and candidate.startswith(normalized[:-2]):
                return True
    return False


def _read(path):
    mode = "rb" if os.path.basename(path) in BINARY_NAMES else "r"
    try:
        if mode == "rb":
            with open(path, "rb") as handle:
                return handle.read()
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


class Target:
    """One lockfile, with its base content and its current content."""

    def __init__(self, label, current, base=None):
        self.label = label
        self.current = current
        self.base = base


def discover(paths, root=None):
    """Expand the given paths into supported lockfiles."""
    found = []
    for path in paths:
        if os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in (".git", "node_modules", "target", "build", ".venv", "venv", "__pycache__")
                ]
                for filename in filenames:
                    full = os.path.join(dirpath, filename)
                    if is_supported(full):
                        found.append(full)
        elif os.path.exists(path):
            found.append(path)
        else:
            found.append(path)  # let the caller report it as missing
    return sorted(set(found))


def build_targets(paths=None, base=None, lock=None, before=None, scan_all=False,
                  cwd=".", exclude=None):
    """Return (targets, errors)."""
    errors = []
    targets = []

    if lock:
        current = _read(lock)
        if current is None:
            errors.append(f"{lock}: cannot read")
            return targets, errors
        base_text = None
        if before:
            base_text = _read(before)
            if base_text is None:
                # No snapshot means the lockfile did not exist before: everything is new.
                base_text = None
        targets.append(Target(lock, current, base_text))
        return targets, errors

    root = gitutil.repo_root(cwd)
    ignores = load_ignores(root, exclude)

    if paths:
        candidates = discover(paths)
        for candidate in candidates:
            if is_ignored(candidate, ignores, root):
                continue
            if not is_supported(candidate):
                errors.append(f"{candidate}: no parser for this filename")
                continue
            current = _read(candidate)
            if current is None:
                errors.append(f"{candidate}: cannot read")
                continue
            base_text = None
            if root and not scan_all:
                relative = os.path.relpath(os.path.abspath(candidate), root)
                ref = base if base else ("HEAD" if gitutil.has_head(root) else None)
                if ref:
                    base_text = gitutil.show(
                        root, ref, relative,
                        binary=os.path.basename(candidate) in BINARY_NAMES,
                    )
            targets.append(Target(candidate, current, base_text))
        return targets, errors

    if not root:
        errors.append(
            "not inside a git repository: pass explicit paths, or --lock/--before"
        )
        return targets, errors

    if base:
        changed = gitutil.changed_paths(root, base)
        for relative in changed:
            if not is_supported(relative) or is_ignored(relative, ignores, root):
                continue
            full = os.path.join(root, relative)
            current = _read(full)
            if current is None:
                continue
            binary = os.path.basename(relative) in BINARY_NAMES
            targets.append(
                Target(relative, current, gitutil.show(root, base, relative, binary=binary))
            )
        return targets, errors

    # Default: staged changes.
    for relative in gitutil.staged_paths(root):
        if not is_supported(relative) or is_ignored(relative, ignores, root):
            continue
        binary = os.path.basename(relative) in BINARY_NAMES
        current = gitutil.show(root, "", relative, binary=binary)
        if current is None:
            continue
        base_text = (
            gitutil.show(root, "HEAD", relative, binary=binary)
            if gitutil.has_head(root)
            else None
        )
        targets.append(Target(relative, current, base_text))
    return targets, errors


def collect(targets, allow_binary_lock=False):
    """Parse every target and return (new_deps, skips, errors)."""
    deps = []
    skips = []
    errors = []
    for target in targets:
        module = parser_for(target.label)
        if module is None:
            errors.append(f"{target.label}: no parser for this filename")
            continue
        current = parse(
            target.label, target.current, target.label,
            allow_binary_lock=allow_binary_lock,
        )
        errors.extend(current.errors)
        skips.extend(current.skips)

        baseline = set()
        if target.base is not None:
            previous = parse(
                target.label, target.base, target.label,
                allow_binary_lock=True,  # the base is history; never fail on it
            )
            baseline = {dep.key for dep in previous.deps}
        deps.extend(dep for dep in current.deps if dep.key not in baseline)
    return deps, skips, errors
