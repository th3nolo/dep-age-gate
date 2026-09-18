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
from dep_age_gate.parsers import requirements
from dep_age_gate.requirements_source import RequirementsSource, SourceError

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
    if parser_for(path) is requirements:
        try:
            root = gitutil.repo_root(path) or os.path.dirname(os.path.abspath(path))
            relative = os.path.relpath(os.path.abspath(path), root).replace(os.sep, "/")
            return RequirementsSource(root).read(relative)
        except SourceError:
            return None
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
        self.current_outcome = None
        self.base_outcome = None


def _show(root, ref, relative, binary=False):
    # Requirements are read later through the bounded snapshot reader.
    if parser_for(relative) is requirements:
        return ""
    return gitutil.show(root, ref, relative, binary=binary)


def _relative_inside(path: str, root: str) -> str | None:
    try:
        relative = os.path.relpath(os.path.abspath(path), root).replace(os.sep, "/")
    except ValueError:
        return None  # Windows paths on different drives cannot be made relative.
    return None if relative == ".." or relative.startswith("../") else relative


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


def _build_targets(paths=None, base=None, lock=None, before=None, scan_all=False,
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
                relative = _relative_inside(candidate, root)
                ref = base if base else ("HEAD" if gitutil.has_head(root) else None)
                if ref and relative is not None:
                    base_text = _show(
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
                Target(relative, current, _show(root, base, relative, binary=binary))
            )
        return targets, errors

    # Default: staged changes.
    for relative in gitutil.staged_paths(root):
        if not is_supported(relative) or is_ignored(relative, ignores, root):
            continue
        binary = os.path.basename(relative) in BINARY_NAMES
        current = _show(root, "", relative, binary=binary)
        if current is None:
            continue
        base_text = (
            _show(root, "HEAD", relative, binary=binary)
            if gitutil.has_head(root)
            else None
        )
        targets.append(Target(relative, current, base_text))
    return targets, errors


def build_targets(paths=None, base=None, lock=None, before=None, scan_all=False,
                  cwd=".", exclude=None):
    """Capture requirements graphs now, using one source for each side of a diff."""
    try:
        root = gitutil.repo_root(cwd)
        if root and base:
            base = gitutil.resolve_commit(root, base)
        targets, errors = _build_targets(paths, base, lock, before, scan_all, cwd, exclude)
        implicit = not paths and not lock
        staged = implicit and not base
        ref = base or (gitutil.resolve_commit(root, "HEAD") if root and gitutil.has_head(root) else None)
        index_entries = gitutil.snapshot_entries(root, "") if staged and root else None
        base_entries = gitutil.snapshot_entries(root, ref) if root and ref else None
        changed = set()
        if implicit and root:
            # Include deletions: a deleted include must fail, not disappear.
            changed = gitutil.changed_including_deleted(root, base)
            if staged:
                previous_entries = base_entries or {}
                changed.update(path for path in index_entries.keys() | previous_entries.keys()
                               if index_entries.get(path) != previous_entries.get(path))
            if changed:
                ignores = load_ignores(root, exclude)
                labels = {target.label for target in targets}
                for relative in index_entries if staged else gitutil.tracked_paths(root):
                    if (parser_for(relative) is requirements and relative not in labels
                            and not is_ignored(relative, ignores, root)):
                        targets.append(Target(relative, ""))

        prepared = []
        graphs: dict[str, set[str]] = {}
        for target in targets:
            if parser_for(target.label) is not requirements:
                prepared.append(target)
                continue
            full = os.path.abspath(os.path.join(root, target.label) if implicit else target.label)
            source_root = root if implicit else (gitutil.repo_root(full) or os.path.dirname(full))
            relative = os.path.relpath(full, source_root).replace(os.sep, "/")
            current_source = RequirementsSource(source_root, "" if staged else None,
                                                entries=index_entries)
            visited: set[str] = set()
            try:
                current_text = current_source.read(relative)
                target.current_outcome = requirements.parse(
                    current_text, relative, snapshot=current_source, visited=visited)
            except SourceError as exc:
                target.current_outcome = ParseOutcome(errors=[
                    f"{target.label}: {exc}; incomplete dependency coverage"])
                visited.add(relative)
            previous_visited: set[str] = set()
            if ref and root == source_root and not scan_all and not lock:
                previous_source = RequirementsSource(source_root, ref, entries=base_entries)
                if relative in previous_source.entries:
                    try:
                        previous_text = previous_source.read(relative)
                        target.base_outcome = requirements.parse(
                            previous_text, relative, snapshot=previous_source, visited=previous_visited)
                    except SourceError:
                        target.base_outcome = ParseOutcome()
            elif target.base is not None:
                # A single --before file cannot prove the historical include graph.
                # Fail-closed text parsing keeps all current pins in coverage.
                target.base_outcome = requirements.parse(target.base, target.label)
            if target.base_outcome is not None and target.base_outcome.errors:
                # A missing/unsupported include may change global source options.
                # Partial history cannot prove any current pin was already audited.
                target.base_outcome = ParseOutcome()
            if implicit:
                graphs[target.label] = visited
            if not implicit or (visited | previous_visited) & changed:
                prepared.append(target)
        if implicit:
            # A supported filename reached via -c is still a constraint, not an
            # independent installation root. Keep cyclic roots so errors surface.
            prepared = [target for target in prepared if not any(
                target.label != parent and target.label in graph
                and parent not in graphs.get(target.label, set())
                for parent, graph in graphs.items()
            )]
        return prepared, errors
    except gitutil.GitError as exc:
        return [], [f"cannot read Git source snapshot: {exc}"]


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
        current = target.current_outcome or parse(
            target.label, target.current, target.label,
            allow_binary_lock=allow_binary_lock,
        )
        errors.extend(current.errors)
        skips.extend(current.skips)

        baseline = set()
        if target.base is not None or target.base_outcome is not None:
            previous = target.base_outcome or parse(
                target.label, target.base, target.label,
                allow_binary_lock=True,  # the base is history; never fail on it
            )
            if module is not requirements or not previous.errors:
                baseline = {dep.key for dep in previous.deps}
        deps.extend(dep for dep in current.deps if dep.key not in baseline)
    return deps, skips, errors
