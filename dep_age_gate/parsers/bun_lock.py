"""bun.lock (text, JSONC) and bun.lockb (binary).

bun.lockb is an undocumented binary format. We do not guess at it: it is
reported as unsupported and the audit fails closed, unless the caller passes
--allow-binary-lock. `bun install --save-text-lockfile` converts a project to
the readable format.
"""

import json

from dep_age_gate.model import NPM, Dep, ParseOutcome, Skip
from dep_age_gate.parsers._common import (
    looks_like_registry_version,
    split_name_version,
    strip_jsonc,
)


def parse(text, source, allow_binary_lock=False, **_):
    outcome = ParseOutcome()
    if str(source).endswith(".lockb") or isinstance(text, bytes) and text[:4] == b"#!/u":
        pass
    if str(source).endswith(".lockb"):
        message = (
            f"{source}: bun.lockb is a binary lockfile and cannot be audited. "
            "Run `bun install --save-text-lockfile` to produce bun.lock, or pass "
            "--allow-binary-lock to skip it."
        )
        if allow_binary_lock:
            outcome.skips.append(Skip(source, "bun.lockb", "binary lockfile skipped by --allow-binary-lock"))
        else:
            outcome.errors.append(message)
        return outcome

    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        data = json.loads(strip_jsonc(text))
    except ValueError as exc:
        outcome.errors.append(f"{source}: not valid JSON/JSONC ({exc})")
        return outcome

    packages = data.get("packages")
    if not isinstance(packages, dict):
        outcome.errors.append(f"{source}: no `packages` object")
        return outcome

    seen = set()
    for key, entry in packages.items():
        resolution = None
        if isinstance(entry, list) and entry:
            resolution = entry[0]
        elif isinstance(entry, str):
            resolution = entry
        if not isinstance(resolution, str):
            outcome.skips.append(Skip(source, key, "no resolution string"))
            continue
        name, version = split_name_version(resolution)
        if not name or not version:
            outcome.skips.append(Skip(source, key, f"unparsable resolution {resolution!r}"))
            continue
        if not looks_like_registry_version(version):
            outcome.skips.append(Skip(source, resolution, "not a registry version"))
            continue
        if (name, version) in seen:
            continue
        seen.add((name, version))
        outcome.deps.append(Dep(NPM, name, version, source, key))
    return outcome
