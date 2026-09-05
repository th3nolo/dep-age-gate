"""pnpm-lock.yaml (lockfileVersion 5.x, 6.x and 9.x).

Only the `packages:` section is read. Its keys carry name and version in three
historic shapes:

  v5   /@eslint/js/8.57.0            and /foo/1.0.0_react@17.0.0
  v6   /@eslint/js@8.57.0
  v9   '@eslint/js@9.0.0'            (no leading slash)
"""

import re

from dep_age_gate.model import NPM, Dep, ParseOutcome, Skip
from dep_age_gate.parsers._common import looks_like_registry_version, split_name_version

_V5 = re.compile(r"^/(?P<name>@?[^@]+?)/(?P<version>\d[^/]*)$")


def _section_keys(text, section):
    """Yield (key, line_number) for the direct children of a top-level section."""
    lines = text.splitlines()
    in_section = False
    child_indent = None
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            in_section = stripped.rstrip() == section + ":"
            child_indent = None
            continue
        if not in_section:
            continue
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue
        if not stripped.endswith(":") and ":" not in stripped:
            continue
        key = stripped
        if key.endswith(":"):
            key = key[:-1]
        else:
            key = key.split(":", 1)[0]
        key = key.strip().strip("'\"")
        if key:
            yield key, number


def parse(text, source, **_):
    outcome = ParseOutcome()
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if "packages:" not in text and "importers:" not in text and "dependencies:" not in text:
        outcome.errors.append(f"{source}: no `packages:` section; not a pnpm lockfile")
        return outcome

    seen = set()
    found_section = False
    for key, _line in _section_keys(text, "packages"):
        found_section = True
        raw = key
        # peer-dependency suffix: eslint@9.0.0(typescript@5.0.0)
        cut = raw.find("(")
        if cut > 0:
            raw = raw[:cut]
        # v5 peer hash suffix: /foo/1.0.0_react@17.0.0
        if "_" in raw and _V5.match(raw.split("_", 1)[0]):
            raw = raw.split("_", 1)[0]

        name = version = None
        match = _V5.match(raw)
        if match:
            name, version = match.group("name"), match.group("version")
        else:
            candidate = raw[1:] if raw.startswith("/") else raw
            name, version = split_name_version(candidate)

        if not name or not version:
            outcome.skips.append(Skip(source, key, "unparsable package key"))
            continue
        if not looks_like_registry_version(version):
            outcome.skips.append(Skip(source, key, "not a registry version"))
            continue
        if (name, version) in seen:
            continue
        seen.add((name, version))
        outcome.deps.append(Dep(NPM, name, version, source, key))

    if not found_section and not outcome.deps:
        outcome.errors.append(
            f"{source}: `packages:` section missing or empty; refusing to report clean"
        )
    return outcome
