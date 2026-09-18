"""Registry identity regressions. All metadata lookups are replaced locally."""

import json

import pytest

from dep_age_gate import audit, cli
from dep_age_gate.model import Result
from dep_age_gate.parsers import parse
from test_audit import git, needs_git

PUBLIC = "https://pypi.org/simple"
PRIVATE = "https://user:secret@private.invalid/simple"


def locktext(path, registry):
    package = '[[package]]\nname = "requests"\nversion = "2.32.3"\n'
    if path == "uv.lock":
        return package + f'source = {{ registry = "{registry}" }}\n'
    if registry is None:
        return package
    return package + f'[package.source]\ntype = "legacy"\nurl = "{registry}"\n'


@pytest.mark.parametrize("path", ["uv.lock", "poetry.lock"])
@pytest.mark.parametrize("registry", [PUBLIC, PUBLIC + "/"])
def test_public_index_is_checked(path, registry):
    outcome = parse(path, locktext(path, registry), path)
    assert [dep.key for dep in outcome.deps] == [("pypi", "requests", "2.32.3")]
    assert outcome.errors == outcome.skips == []


@pytest.mark.parametrize("path", ["uv.lock", "poetry.lock"])
@pytest.mark.parametrize("registry", [
    PRIVATE, "https://pypi.org.evil.invalid/simple", "http://pypi.org/simple",
    "https://pypi.org/simple?index=private", "https://pypi.org/other", "",
    "https://pypi.org@private.invalid/simple",
])
def test_unsupported_index_is_an_error_without_exposing_credentials(path, registry):
    outcome = parse(path, locktext(path, registry), path)
    assert outcome.deps == outcome.skips == []
    assert len(outcome.errors) == 1
    assert "requests==2.32.3: unsupported registry" in outcome.errors[0]
    assert "secret" not in outcome.errors[0]
    assert "private.invalid" not in outcome.errors[0]


def test_uv_missing_registry_value_fails_closed():
    text = locktext("uv.lock", PUBLIC).replace(f'"{PUBLIC}"', "123")
    outcome = parse("uv.lock", text, "uv.lock")
    assert outcome.deps == []
    assert "unsupported registry" in outcome.errors[0]


def test_uv_literal_string_and_comment_are_supported():
    text = locktext("uv.lock", PUBLIC).replace(f'"{PUBLIC}"', f"'{PUBLIC}'")
    text = text.rstrip() + " # registry\n"
    assert len(parse("uv.lock", text, "uv.lock").deps) == 1


def test_poetry_source_url_is_not_taken_from_another_table():
    text = locktext("poetry.lock", PUBLIC).replace(f'url = "{PUBLIC}"', "")
    text += f'[package.dependencies]\nurl = "{PUBLIC}"\n'
    outcome = parse("poetry.lock", text, "poetry.lock")
    assert outcome.deps == []
    assert "unsupported registry" in outcome.errors[0]


@pytest.mark.parametrize("path", ["uv.lock", "poetry.lock"])
@pytest.mark.parametrize("before,current,checked,error", [
    (PUBLIC, PRIVATE, 0, True),
    (PRIVATE, PUBLIC, 1, False),
    (PRIVATE, PRIVATE, 0, True),
    (PRIVATE, "https://second.invalid/simple", 0, True),
    (PUBLIC, PUBLIC, 0, False),
    (PUBLIC, PUBLIC + "/", 0, False),
    (None, PRIVATE, 0, True),
])
def test_same_version_registry_changes_in_audit(path, before, current, checked, error):
    baseline = locktext(path, before) if before is not None else None
    deps, skips, errors = audit.collect([audit.Target(path, locktext(path, current), baseline)])
    assert len(deps) == checked
    assert bool(errors) is error
    assert skips == []


@pytest.mark.parametrize("path", ["uv.lock", "poetry.lock"])
@pytest.mark.parametrize("before,current,checked,error", [
    (PUBLIC, PRIVATE, 0, True), (PRIVATE, PUBLIC, 1, False),
    (PRIVATE, PRIVATE, 0, True), (None, PRIVATE, 0, True),
])
@pytest.mark.parametrize("json_output", [False, True])
def test_cli_rejects_custom_registry_before_metadata_lookup(
    path, before, current, checked, error, json_output, tmp_path, monkeypatch, capsys,
):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def evaluate(self, deps, *args, **kwargs):
            calls.extend(deps)
            return [Result(dep=dep, status="PASS") for dep in deps]

    monkeypatch.setattr(cli, "RegistryClient", FakeClient)
    lock = tmp_path / path
    lock.write_text(locktext(path, current), encoding="utf-8")
    args = ["audit", "--lock", str(lock)]
    if before is not None:
        baseline = tmp_path / "before.lock"
        baseline.write_text(locktext(path, before), encoding="utf-8")
        args += ["--before", str(baseline)]
    else:
        args += ["--all"]
    if json_output:
        args += ["--json"]
    assert cli.main(args) == int(error)
    assert len(calls) == checked
    captured = capsys.readouterr()
    assert "secret" not in captured.out + captured.err
    if json_output:
        payload = json.loads(captured.out)
        assert bool(payload["errors"]) is error
        assert len(payload["results"]) == checked
    elif error:
        assert "unsupported registry" in captured.err


@needs_git
@pytest.mark.parametrize("base_mode", [False, True])
@pytest.mark.parametrize("before,current,checked,error", [
    (PUBLIC, PRIVATE, 0, True), (PRIVATE, PUBLIC, 1, False),
])
def test_git_registry_only_diff(tmp_path, monkeypatch, base_mode, before, current, checked, error):
    git(tmp_path, "init", "-q")
    lock = tmp_path / "uv.lock"
    lock.write_text(locktext("uv.lock", before), encoding="utf-8")
    git(tmp_path, "add", "uv.lock")
    git(tmp_path, "commit", "-qm", "baseline")
    lock.write_text(locktext("uv.lock", current), encoding="utf-8")
    if not base_mode:
        git(tmp_path, "add", "uv.lock")
    monkeypatch.chdir(tmp_path)
    targets, errors = audit.build_targets(base="HEAD" if base_mode else None)
    assert errors == []
    assert len(targets) == 1
    deps, skips, errors = audit.collect(targets)
    assert len(deps) == checked
    assert bool(errors) is error
    assert skips == []


def test_uv_custom_source_cannot_be_overridden_by_a_nested_source():
    text = locktext("uv.lock", PRIVATE)
    text += '[package.metadata]\nsource = { registry = "https://pypi.org/simple" }\n'
    outcome = parse("uv.lock", text, "uv.lock")
    assert outcome.deps == []
    assert "unsupported registry" in outcome.errors[0]


def test_mixed_indexes_do_not_hide_the_unsupported_copy():
    text = locktext("uv.lock", PUBLIC) + locktext("uv.lock", PRIVATE)
    deps, skips, errors = audit.collect([audit.Target("uv.lock", text, text)])
    assert deps == skips == []
    assert len(errors) == 1
    assert "unsupported registry" in errors[0]
