"""yarn.lock, both the v1 classic format and the Berry (v2+) format.

Berry entries are read from `resolution: "name@npm:version"`, which is the one
place the real resolved protocol is recorded. Anything that is not `npm:`
(workspace:, patch:, portal:, link:, file:, https:) has no registry publish
date and is skipped.

v1 keys can be npm aliases, `string-width-cjs@npm:string-width@^4.2.0`. The
block's version then belongs to the real package (`string-width`), and that is
the name that is checked. An alias key that cannot be read fails closed
(UNKNOWN), it is never skipped.
"""

import re

from dep_age_gate.model import NPM, Dep, ParseOutcome, Skip
from dep_age_gate.parsers._common import (
    looks_like_registry_version,
    parse_npm_alias,
    split_name_version,
)

_BERRY_RESOLUTION = re.compile(r'^\s+resolution:\s*"(?P<value>[^"]+)"\s*$')
_V1_VERSION = re.compile(r'^\s+version\s+"?(?P<value>[^"\s]+)"?\s*$')
_V1_RESOLVED = re.compile(r'^\s+resolved\s+"?(?P<value>[^"\s]+)"?\s*$')


def _is_berry(text):
    return "__metadata:" in text or re.search(r"^\s+resolution:", text, re.MULTILINE)


def parse(text, source, **_):
    outcome = ParseOutcome()
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if _is_berry(text):
        return _parse_berry(text, source, outcome)
    return _parse_v1(text, source, outcome)


def _parse_berry(text, source, outcome):
    seen = set()
    found = False
    for line in text.splitlines():
        match = _BERRY_RESOLUTION.match(line)
        if not match:
            continue
        found = True
        value = match.group("value")
        at = value.rfind("@")
        if at <= 0:
            outcome.skips.append(Skip(source, value, "unparsable resolution"))
            continue
        name, locator = value[:at], value[at + 1:]
        if not locator.startswith("npm:"):
            outcome.skips.append(Skip(source, value, f"non-npm protocol {locator.split(':', 1)[0]}:"))
            continue
        version = locator[4:]
        if not looks_like_registry_version(version):
            outcome.skips.append(Skip(source, value, "not a registry version"))
            continue
        if (name, version) in seen:
            continue
        seen.add((name, version))
        outcome.deps.append(Dep(NPM, name, version, source, value))
    if not found:
        outcome.errors.append(f"{source}: berry lockfile with no `resolution:` lines")
    return outcome


def _parse_v1(text, source, outcome):
    seen = set()
    descriptors = []
    version = None
    resolved = None

    def flush():
        if not descriptors or not version:
            return
        for descriptor in descriptors:
            alias = parse_npm_alias(descriptor)
            if alias is not None and alias[1] is None:
                # An alias key we cannot read. Keep the raw key as the name:
                # it is not a valid npm name, so the lookup grades it UNKNOWN
                # and the audit fails closed.
                outcome.deps.append(
                    Dep(NPM, descriptor, version, source, "malformed npm alias")
                )
                continue
            if alias is not None:
                # `<alias>@npm:<real>@<range>`: the block's version belongs to <real>.
                name = alias[1]
            else:
                name, _range = split_name_version(descriptor)
            if not name:
                outcome.skips.append(Skip(source, descriptor, "unparsable descriptor"))
                continue
            if resolved and not resolved.startswith(("http://", "https://")):
                outcome.skips.append(Skip(source, descriptor, f"non-registry source {resolved}"))
                continue
            if not looks_like_registry_version(version):
                outcome.skips.append(Skip(source, f"{name}@{version}", "not a registry version"))
                continue
            if (name, version) in seen:
                continue
            seen.add((name, version))
            outcome.deps.append(Dep(NPM, name, version, source, descriptor))
            # No break: with npm aliases one block can name the same package
            # under several keys, and every key must be read so a malformed
            # one is never hidden behind a good one. `seen` drops repeats.

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and not line.startswith("\t"):
            flush()
            descriptors = []
            version = None
            resolved = None
            head = line.rstrip()
            if head.endswith(":"):
                head = head[:-1]
            descriptors = [part.strip().strip('"') for part in head.split(",") if part.strip()]
            continue
        match = _V1_VERSION.match(line)
        if match:
            version = match.group("value")
            continue
        match = _V1_RESOLVED.match(line)
        if match:
            resolved = match.group("value")
    flush()
    if not outcome.deps and not outcome.skips:
        outcome.errors.append(f"{source}: yarn v1 lockfile with no version entries")
    return outcome
