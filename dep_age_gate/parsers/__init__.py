"""Lockfile parsers.

Every parser takes (text, source_label) and returns a ParseOutcome holding the
registry-backed dependency versions it found, plus explicit skips for entries
that have no registry publish date (workspace members, git and path deps).

Fail closed: a format we cannot read becomes an error, never an empty result.
"""

import fnmatch
import os

from dep_age_gate.model import ParseOutcome
from dep_age_gate.parsers import (
    bun_lock,
    cargo_lock,
    gradle_lock,
    npm_lock,
    pnpm_lock,
    pom,
    poetry_lock,
    requirements,
    uv_lock,
    yarn_lock,
)

# filename pattern -> module. Order matters: first match wins.
_ROUTES = [
    ("package-lock.json", npm_lock),
    ("npm-shrinkwrap.json", npm_lock),
    ("pnpm-lock.yaml", pnpm_lock),
    ("pnpm-lock.yml", pnpm_lock),
    ("bun.lock", bun_lock),
    ("bun.lockb", bun_lock),
    ("yarn.lock", yarn_lock),
    ("uv.lock", uv_lock),
    ("poetry.lock", poetry_lock),
    ("Cargo.lock", cargo_lock),
    ("gradle.lockfile", gradle_lock),
    ("pom.xml", pom),
    ("requirements*.txt", requirements),
    ("requirements*.in", requirements),
]

SUPPORTED_GLOBS = [pattern for pattern, _ in _ROUTES]


def parser_for(path: str):
    name = os.path.basename(str(path))
    for pattern, module in _ROUTES:
        if fnmatch.fnmatch(name, pattern):
            return module
    return None


def is_supported(path: str) -> bool:
    return parser_for(path) is not None


def parse(path: str, text, source_label: str = None, **kwargs) -> ParseOutcome:
    module = parser_for(path)
    if module is None:
        outcome = ParseOutcome()
        outcome.errors.append(f"{path}: no parser for this filename")
        return outcome
    return module.parse(text, source_label or str(path), **kwargs)
