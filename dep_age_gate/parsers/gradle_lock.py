"""gradle.lockfile — `group:artifact:version=configuration,configuration`."""

from dep_age_gate.model import MAVEN, Dep, ParseOutcome, Skip

def parse(text, source, **_):
    outcome = ParseOutcome()
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    seen = set()
    saw_line = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        saw_line = True
        if line.startswith("empty="):
            continue
        coordinate = line.split("=", 1)[0].strip()
        parts = coordinate.split(":")
        if len(parts) != 3 or not all(parts):
            outcome.skips.append(Skip(source, line[:60], "not a group:artifact:version line"))
            continue
        group, artifact, version = parts
        key = (group, artifact, version)
        if key in seen:
            continue
        seen.add(key)
        configurations = line.split("=", 1)[1].strip() if "=" in line else ""
        outcome.deps.append(
            Dep(MAVEN, f"{group}:{artifact}", version, source, configurations)
        )
    if not saw_line:
        outcome.errors.append(f"{source}: empty gradle lockfile")
    return outcome
