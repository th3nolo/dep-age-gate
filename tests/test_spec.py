"""parse_spec: the four spec shapes and the errors."""

import pytest

from dep_age_gate.model import CRATES, MAVEN, NPM, PYPI
from dep_age_gate.spec import SpecError, parse_spec


def test_npm():
    dep = parse_spec("npm:vitest@5.0.0")
    assert dep.key == (NPM, "vitest", "5.0.0")
    assert dep.source == "<cli>"


def test_npm_scoped_name_keeps_the_scope():
    dep = parse_spec("npm:@types/node@22.0.0")
    assert dep.key == (NPM, "@types/node", "22.0.0")


def test_pypi():
    assert parse_spec("pypi:requests@2.32.0").key == (PYPI, "requests", "2.32.0")


def test_crates():
    assert parse_spec("crates:serde@1.0.200").key == (CRATES, "serde", "1.0.200")


def test_maven_three_part_coordinate():
    dep = parse_spec("maven:org.apache.commons:commons-lang3:3.14.0")
    assert dep.key == (MAVEN, "org.apache.commons:commons-lang3", "3.14.0")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("pip:requests@2.32.0", (PYPI, "requests", "2.32.0")),
        ("cargo:serde@1.0.200", (CRATES, "serde", "1.0.200")),
        ("gradle:com.google.guava:guava:33.0.0-jre", (MAVEN, "com.google.guava:guava", "33.0.0-jre")),
        ("NPM:vitest@5.0.0", (NPM, "vitest", "5.0.0")),
    ],
)
def test_aliases(text, expected):
    assert parse_spec(text).key == expected


def test_label():
    assert parse_spec("npm:vitest@5.0.0").label() == "npm:vitest@5.0.0"


def test_no_prefix():
    with pytest.raises(SpecError, match="missing ecosystem prefix"):
        parse_spec("vitest@5.0.0")


def test_unknown_prefix():
    with pytest.raises(SpecError, match="unknown ecosystem"):
        parse_spec("nuget:Newtonsoft.Json@13.0.3")


@pytest.mark.parametrize(
    "text",
    [
        "maven:org.apache.commons:3.14.0",
        "maven:org.apache.commons:commons-lang3:3.14.0:extra",
        "maven:org.apache.commons::3.14.0",
        "maven:commons-lang3",
    ],
)
def test_maven_wrong_arity(text):
    with pytest.raises(SpecError, match="maven specs are"):
        parse_spec(text)


@pytest.mark.parametrize("text", ["npm:vitest", "npm:@types/node", "pypi:requests@", "npm:@1.0.0"])
def test_missing_version(text):
    with pytest.raises(SpecError, match="expected <name>@<version>"):
        parse_spec(text)
