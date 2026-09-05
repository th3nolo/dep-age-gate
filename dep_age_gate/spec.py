"""Parse command-line package specs.

  npm:vitest@5.0.0
  npm:@types/node@22.0.0
  pypi:requests@2.32.0
  crates:serde@1.0.200
  maven:org.apache.commons:commons-lang3:3.14.0
"""

from dep_age_gate.model import Dep, ECOSYSTEM_ALIASES, MAVEN


class SpecError(ValueError):
    pass


def parse_spec(text: str) -> Dep:
    if ":" not in text:
        raise SpecError(
            f"{text!r}: missing ecosystem prefix "
            "(npm: / pypi: / crates: / maven:)"
        )
    prefix, rest = text.split(":", 1)
    eco = ECOSYSTEM_ALIASES.get(prefix.strip().lower())
    if eco is None:
        raise SpecError(f"{text!r}: unknown ecosystem {prefix!r}")

    if eco == MAVEN:
        parts = rest.split(":")
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise SpecError(
                f"{text!r}: maven specs are maven:<groupId>:<artifactId>:<version>"
            )
        group, artifact, version = (p.strip() for p in parts)
        return Dep(MAVEN, f"{group}:{artifact}", version, source="<cli>")

    at = rest.rfind("@")
    if at <= 0:
        raise SpecError(f"{text!r}: expected <name>@<version>")
    name, version = rest[:at].strip(), rest[at + 1:].strip()
    if not name or not version:
        raise SpecError(f"{text!r}: expected <name>@<version>")
    return Dep(eco, name, version, source="<cli>")
