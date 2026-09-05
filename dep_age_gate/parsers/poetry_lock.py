"""poetry.lock — TOML `[[package]]` blocks, optionally with [package.source]."""

import re

from dep_age_gate.model import PYPI, Dep, ParseOutcome, Skip
from dep_age_gate.parsers._common import iter_toml_package_blocks, toml_string

_SOURCE_TABLE = re.compile(r"^\s*\[package\.source\]\s*$", re.MULTILINE)


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
        if _SOURCE_TABLE.search(block):
            kind = toml_string(block, "type") or "custom"
            if kind not in ("legacy",):  # `legacy` = an alternate PyPI index
                outcome.skips.append(
                    Skip(source, f"{name}=={version}", f"[package.source] type = {kind}")
                )
                continue
        outcome.deps.append(Dep(PYPI, name, version, source))
    return outcome
