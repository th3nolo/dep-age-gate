"""package-lock.json / npm-shrinkwrap.json (lockfileVersion 1, 2 and 3)."""

import json
import re

from dep_age_gate.model import NPM, Dep, ParseOutcome, Skip
from dep_age_gate.parsers._common import looks_like_registry_version

_NAME_FROM_PATH = re.compile(r"(?:^|/)node_modules/(.+)$")


def _name_from_path(path: str):
    match = _NAME_FROM_PATH.search(path)
    if not match:
        return None
    return match.group(1)


def parse(text, source, **_):
    outcome = ParseOutcome()
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError as exc:
        outcome.errors.append(f"{source}: not valid JSON ({exc})")
        return outcome
    if not isinstance(data, dict):
        outcome.errors.append(f"{source}: top level is not an object")
        return outcome

    version = data.get("lockfileVersion")
    packages = data.get("packages")
    if isinstance(packages, dict) and packages:
        _walk_packages(packages, source, outcome)
    elif isinstance(data.get("dependencies"), dict):
        # lockfileVersion 1
        _walk_v1(data["dependencies"], source, outcome)
    elif version is None and "dependencies" not in data:
        outcome.errors.append(
            f"{source}: no `packages` or `dependencies` section; "
            "unrecognised package-lock format"
        )
    return outcome


def _walk_packages(packages, source, outcome):
    for path, node in packages.items():
        if not isinstance(node, dict):
            continue
        if path == "":
            continue  # the root project itself
        if node.get("link") is True:
            outcome.skips.append(Skip(source, path, "workspace link"))
            continue
        if _name_from_path(path) is None:
            # A key with no node_modules/ segment is a workspace member's own
            # package block (e.g. "packages/ui"), not an installed dependency.
            # It has no registry release, so checking it reports UNKNOWN for
            # code that lives in this repo.
            outcome.skips.append(Skip(source, path, "workspace member, not a registry install"))
            continue
        name = node.get("name") or _name_from_path(path)
        version = node.get("version")
        if not name or not version:
            outcome.skips.append(Skip(source, path, "no name/version"))
            continue
        resolved = node.get("resolved") or ""
        if resolved and not resolved.startswith(("http://", "https://")):
            outcome.skips.append(Skip(source, f"{name}@{version}", f"non-registry source {resolved}"))
            continue
        if resolved and "/-/" not in resolved and "registry" not in resolved:
            outcome.skips.append(Skip(source, f"{name}@{version}", f"non-registry tarball {resolved}"))
            continue
        if not looks_like_registry_version(version):
            outcome.skips.append(Skip(source, f"{name}@{version}", "not a registry version"))
            continue
        outcome.deps.append(Dep(NPM, name, version, source, path))


def _walk_v1(deps, source, outcome, prefix=""):
    for name, node in deps.items():
        if not isinstance(node, dict):
            continue
        version = node.get("version")
        if version and looks_like_registry_version(version):
            outcome.deps.append(Dep(NPM, name, version, source, prefix + name))
        elif version:
            outcome.skips.append(Skip(source, f"{name}@{version}", "not a registry version"))
        if isinstance(node.get("dependencies"), dict):
            _walk_v1(node["dependencies"], source, outcome, prefix + name + "/")
