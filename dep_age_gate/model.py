"""Shared data types."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

NPM = "npm"
PYPI = "pypi"
CRATES = "crates"
MAVEN = "maven"

ECOSYSTEMS = (NPM, PYPI, CRATES, MAVEN)

# Aliases accepted on the command line for `check`.
ECOSYSTEM_ALIASES = {
    "npm": NPM, "node": NPM, "js": NPM,
    "pypi": PYPI, "pip": PYPI, "py": PYPI, "python": PYPI,
    "crates": CRATES, "cratesio": CRATES, "cargo": CRATES, "rust": CRATES,
    "maven": MAVEN, "mvn": MAVEN, "gradle": MAVEN, "java": MAVEN,
}


@dataclass(frozen=True)
class Dep:
    """One dependency version found in a lockfile or on the command line."""

    ecosystem: str
    name: str
    version: str
    source: str = ""      # file it came from
    detail: str = ""      # extra context (configuration name, lock section)

    @property
    def key(self) -> tuple:
        return (self.ecosystem, self.name, self.version)

    def label(self) -> str:
        return f"{self.ecosystem}:{self.name}@{self.version}"


@dataclass
class Result:
    dep: Dep
    published: Optional[datetime] = None
    age_seconds: Optional[float] = None
    status: str = "UNKNOWN"        # PASS | FAIL | UNKNOWN | ALLOWED | SKIP
    message: str = ""
    allow_reason: str = ""
    from_cache: bool = False

    @property
    def failed(self) -> bool:
        return self.status in ("FAIL", "UNKNOWN")


@dataclass
class Skip:
    """A dependency we deliberately did not check, with the reason."""

    source: str
    what: str
    reason: str


@dataclass
class ParseOutcome:
    deps: list = field(default_factory=list)
    skips: list = field(default_factory=list)
    errors: list = field(default_factory=list)
