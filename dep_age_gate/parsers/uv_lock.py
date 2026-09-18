"""uv.lock — TOML with one `[[package]]` block per resolved distribution.

Only public PyPI registry packages are checked. Other registries fail closed;
editable, virtual, directory and git sources have no PyPI upload time.
"""

import re

from dep_age_gate.model import PYPI, Dep, ParseOutcome, Skip
from dep_age_gate.parsers._common import PYPI_INDEXES, iter_toml_package_blocks, toml_string

_SOURCE = re.compile(r"^\s*source\s*=\s*\{\s*([a-z-]+)\s*=", re.MULTILINE)
_REGISTRY = re.compile(
    r'''^\s*source\s*=\s*\{\s*registry\s*=\s*(["'])([^"'\r\n]*)\1\s*\}\s*(?:\#.*)?$''',
    re.MULTILINE,
)


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
        match = _SOURCE.search(block)
        kind = match.group(1) if match else None
        if kind != "registry":
            outcome.skips.append(
                Skip(source, f"{name}=={version}", f"source is {kind or 'unset'}, not a registry")
            )
            continue
        registry = _REGISTRY.match(block, match.start())
        if registry is None or registry.group(2) not in PYPI_INDEXES:
            # Do not include URLs here: private indexes may embed credentials.
            # Excluding these deps also keeps unsupported baseline entries from
            # hiding a later same-version switch back to public PyPI.
            outcome.errors.append(
                f"{source}: {name}=={version}: unsupported registry; "
                "only https://pypi.org/simple is supported"
            )
            continue
        outcome.deps.append(Dep(PYPI, name, version, source))
    return outcome
