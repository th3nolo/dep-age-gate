"""Requirements exact pins and bounded local include graphs.

Text-only callers fail closed on includes. Audit supplies a matching source
snapshot, never an implicit filesystem fallback for historical content.
This is an age audit, not pip's resolver; unsupported constraints fail closed.
"""

import re
import shlex

from dep_age_gate.model import PYPI, Dep, ParseOutcome, Skip
from dep_age_gate.requirements_source import MAX_DEPTH, RequirementsSource, SourceError, include_path

_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"\s*==\s*(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)
_ANY_REQ = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")


def _directive_kind(line):
    # Recognize pip's attached short arguments and long --option=value forms.
    # Never echo arguments: index URLs can contain credentials.
    option = line.split(None, 1)[0].split("=", 1)[0]
    if option == "--requirement" or line.startswith("-r") and not line.startswith("--"):
        return "requirements include (-r/--requirement)"
    if option == "--constraint" or line.startswith("-c") and not line.startswith("--"):
        return "constraints include (-c/--constraint)"
    if option == "--editable" or line.startswith("-e") and not line.startswith("--"):
        return "editable requirement (-e/--editable)"
    return "requirements option"


def parse(text, source, *, snapshot: RequirementsSource | None = None,
          source_path: str | None = None, visited: set[str] | None = None, **_):
    if snapshot is not None:
        return _parse_graph(text, source_path or source, snapshot, visited)
    return _parse_text(text, source)


def _parse_text(text, source):
    outcome = ParseOutcome()
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")

    # Join backslash continuations (used by --hash blocks).
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    for raw in joined.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split(" #", 1)[0].strip()
        if line.startswith("-"):
            outcome.errors.append(
                f"{source}: unsupported {_directive_kind(line)}; "
                "incomplete dependency coverage. Audit a self-contained file of "
                "exact pins without option lines, or a supported lockfile."
            )
            continue
        body = line.split(";", 1)[0].strip()  # drop the environment marker
        body = _without_hashes(body)
        if re.search(r"\s--", body):
            outcome.errors.append(f"{source}: unsupported per-requirement option; incomplete dependency coverage")
            continue
        if "://" in line:
            outcome.skips.append(Skip(source, line[:60], "direct URL requirement"))
            continue
        match = _PIN.match(body)
        if match:
            outcome.deps.append(
                Dep(PYPI, match.group("name"), match.group("version"), source)
            )
            continue
        other = _ANY_REQ.match(body)
        if other:
            outcome.skips.append(
                Skip(source, body[:60], "not pinned with == ; no single version to date")
            )
        else:
            outcome.errors.append(f"{source}: unsupported requirement syntax; incomplete dependency coverage")
    return outcome


_INCLUDE = re.compile(r"^(?:--(requirement|constraint)(?:=|\s+)|-([rc])\s*)(.*)$")
_BARE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?$")
_EXACT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)$")


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _without_hashes(body: str) -> str:
    return re.sub(r"\s+--hash(?:=|\s+)[A-Za-z0-9]+:[A-Fa-f0-9]+(?=\s|$)", "", body).strip()


def _parse_graph(text: str | bytes, source: str, snapshot: RequirementsSource,
                 visited: set[str] | None) -> ParseOutcome:
    outcome = ParseOutcome()
    requirements: list[tuple[str, str]] = []
    constraints: dict[str, set[str]] = {}
    seen: set[tuple[str, bool]] = set()
    active: set[str] = set()

    def error(path: str, reason: str) -> None:
        outcome.errors.append(f"{path}: {reason}; incomplete dependency coverage")

    def visit(path: str, content: str | bytes, constraint: bool, depth: int) -> None:
        if visited is not None:
            visited.add(path)
        if path in active:
            error(path, "requirements include cycle")
            return
        if depth > MAX_DEPTH:
            error(path, "requirements include depth limit exceeded")
            return
        if (path, constraint) in seen:
            return
        seen.add((path, constraint))
        active.add(path)
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8-sig")
            except UnicodeError:
                error(path, "requirements must be UTF-8")
                active.remove(path)
                return
        # Join before removing comments, as pip does. Do not join comment lines.
        content = re.sub(r"(?m)^(?!\s*#)(.*)\\\r?\n", r"\1 ", content)
        for raw in content.splitlines():
            line = re.split(r"(^|\s+)#", raw, maxsplit=1)[0].strip()
            if not line:
                continue
            match = _INCLUDE.match(line)
            if match:
                try:
                    # Preserve Windows separators; accept a quoted path with spaces.
                    tokens = shlex.split(match[3], posix=False)
                    if len(tokens) != 1:
                        raise SourceError("include requires exactly one local path")
                    argument = tokens[0]
                    if argument[:1] in ("'", '"'):
                        argument = argument[1:-1]
                    child = include_path(path, argument)
                    if visited is not None:
                        visited.add(child)
                    child_text = snapshot.read(child)
                    visit(child, child_text, (match[1] or match[2]) in ("constraint", "c"), depth + 1)
                except (SourceError, ValueError) as exc:
                    # Never print the argument: remote URLs may contain credentials.
                    error(path, str(exc) if isinstance(exc, SourceError) else "invalid include syntax")
                continue
            if line.startswith("-"):
                error(path, f"unsupported {_directive_kind(line)}")
            elif constraint:
                pin = _EXACT.fullmatch(line)
                if pin is None:
                    error(path, "constraints require unconditional exact pins without extras")
                else:
                    constraints.setdefault(_name(pin[1]), set()).add(pin[2])
            else:
                requirements.append((path, line))
        active.remove(path)

    visit(source, text, False, 0)
    for path, line in requirements:
        name_match = _ANY_REQ.match(line)
        constrained = constraints.get(_name(name_match["name"])) if name_match else None
        if constrained:
            if len(constrained) != 1:
                error(path, "conflicting exact constraints")
                continue
            version = next(iter(constrained))
            body = _without_hashes(line.split(";", 1)[0].strip())
            if _BARE.fullmatch(body):
                outcome.deps.append(Dep(PYPI, name_match["name"], version, path))
                continue
            pin = _PIN.match(body)
            if pin is None or pin["version"] != version:
                error(path, "requirement cannot be proven compatible with exact constraint; compile to pins")
                continue
        parsed = _parse_text(line, path)
        outcome.deps.extend(parsed.deps)
        outcome.skips.extend(parsed.skips)
        outcome.errors.extend(parsed.errors)
    return outcome
