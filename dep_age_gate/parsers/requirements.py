"""requirements*.txt — only exact `==` pins can be checked.

A range (`>=2.0`) has no single version to date, so it is reported as a skip;
that is a real hole and the README says so. Pin with `pip-compile` / `uv pip
compile` to close it.
"""

import re

from dep_age_gate.model import PYPI, Dep, ParseOutcome, Skip

_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"\s*==\s*(?P<version>[^\s;#,]+)"
)
_ANY_REQ = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")


def parse(text, source, **_):
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
        if line.startswith("-") or line.startswith("--"):
            continue  # -r, -c, -e, --index-url, --hash on its own line
        if "://" in line:
            outcome.skips.append(Skip(source, line[:60], "direct URL requirement"))
            continue
        body = line.split(";", 1)[0].strip()  # drop the environment marker
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
    return outcome
