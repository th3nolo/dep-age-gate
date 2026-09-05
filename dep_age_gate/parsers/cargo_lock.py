"""Cargo.lock — TOML `[[package]]` blocks.

Only crates.io packages are checked. A package with no `source` is a path
member of the workspace; a `git+` source has no crates.io release.
"""

from dep_age_gate.model import CRATES, Dep, ParseOutcome, Skip
from dep_age_gate.parsers._common import iter_toml_package_blocks, toml_string

CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"


def parse(text, source, **_):
    outcome = ParseOutcome()
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    blocks = list(iter_toml_package_blocks(text))
    if not blocks:
        outcome.errors.append(f"{source}: no [[package]] blocks found")
        return outcome
    for block in blocks:
        name = toml_string(block, "name")
        version = toml_string(block, "version")
        if not name or not version:
            continue
        origin = toml_string(block, "source")
        if origin is None:
            outcome.skips.append(Skip(source, f"{name} {version}", "workspace/path member"))
            continue
        if origin != CRATES_IO:
            outcome.skips.append(Skip(source, f"{name} {version}", f"non-crates.io source {origin}"))
            continue
        outcome.deps.append(Dep(CRATES, name, version, source))
    return outcome
