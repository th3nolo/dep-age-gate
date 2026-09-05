"""cli.main with the registry client replaced. No test here touches the network.

Console output is read with capsys, which proves the report goes to the
current sys.stdout rather than to a stream captured at import time.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from dep_age_gate import cli
from dep_age_gate.model import Result

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)


def install_fake_registry(monkeypatch, ages):
    """Replace cli.RegistryClient with one that grades from a canned age table.

    `ages` maps (ecosystem, name, version) -> age in seconds. A key that is
    missing grades UNKNOWN, exactly as a version the registry does not know.
    """
    calls = []

    class FakeRegistryClient:
        def __init__(self, cache=None, transport=None, workers=12):
            self.cache = cache
            self.workers = workers

        def evaluate(self, deps, min_age_seconds, now=None, allow=None):
            allow = allow or {}
            calls.append((list(deps), min_age_seconds, dict(allow)))
            results = []
            for dep in deps:
                if dep.key in allow:
                    results.append(
                        Result(dep=dep, status="ALLOWED", allow_reason=allow[dep.key])
                    )
                    continue
                age = ages.get(dep.key)
                if age is None:
                    results.append(
                        Result(dep=dep, status="UNKNOWN", message="version not in registry")
                    )
                    continue
                results.append(
                    Result(
                        dep=dep,
                        published=NOW - timedelta(seconds=age),
                        age_seconds=float(age),
                        status="PASS" if age >= min_age_seconds else "FAIL",
                    )
                )
            return results

    monkeypatch.setattr(cli, "RegistryClient", FakeRegistryClient)
    return calls


def lockfile(entries):
    packages = {"": {"name": "my-app", "version": "1.0.0"}}
    for name, version in entries:
        packages[f"node_modules/{name}"] = {
            "version": version,
            "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz",
        }
    return json.dumps({"lockfileVersion": 3, "packages": packages}) + "\n"


# ---------------------------------------------------------------- check


def test_check_returns_1_when_a_version_is_too_young(monkeypatch, capsys):
    install_fake_registry(monkeypatch, {("npm", "left-pad", "1.3.0"): 10 * 3600})
    code = cli.main(["check", "npm:left-pad@1.3.0"])
    assert code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "npm:left-pad@1.3.0" in out


def test_check_returns_0_when_the_version_is_old_enough(monkeypatch, capsys):
    install_fake_registry(monkeypatch, {("npm", "left-pad", "1.3.0"): 30 * 86400})
    assert cli.main(["check", "npm:left-pad@1.3.0"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_check_returns_1_for_an_unknown_version(monkeypatch, capsys):
    install_fake_registry(monkeypatch, {})
    assert cli.main(["check", "npm:left-pad@9.9.9"]) == 1
    assert "UNKNOWN" in capsys.readouterr().out


def test_check_honours_min_age(monkeypatch, capsys):
    install_fake_registry(monkeypatch, {("npm", "left-pad", "1.3.0"): 10 * 3600})
    assert cli.main(["check", "npm:left-pad@1.3.0", "--min-age", "1h"]) == 0
    assert cli.main(["check", "npm:left-pad@1.3.0", "--min-age", "3d"]) == 1


def test_check_passes_the_parsed_min_age_to_the_client(monkeypatch, capsys):
    calls = install_fake_registry(monkeypatch, {("npm", "left-pad", "1.3.0"): 10 * 3600})
    cli.main(["check", "npm:left-pad@1.3.0", "--min-age", "PT72H"])
    assert calls[0][1] == 259200


def test_check_rejects_a_bad_spec(monkeypatch):
    install_fake_registry(monkeypatch, {})
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["check", "left-pad@1.3.0"])
    assert "missing ecosystem prefix" in str(excinfo.value)


def test_check_rejects_a_bad_min_age(monkeypatch):
    install_fake_registry(monkeypatch, {})
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["check", "npm:left-pad@1.3.0", "--min-age", "72x"])
    assert "unknown duration unit" in str(excinfo.value)


# ---------------------------------------------------------------- --json


def test_check_json_has_the_documented_keys(monkeypatch, capsys):
    install_fake_registry(monkeypatch, {("npm", "left-pad", "1.3.0"): 10 * 3600})
    code = cli.main(["check", "npm:left-pad@1.3.0", "--min-age", "72h", "--json"])
    assert code == 1

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"min_age_seconds", "summary", "results", "skipped", "errors"}
    assert payload["min_age_seconds"] == 259200
    assert payload["summary"] == {"PASS": 0, "FAIL": 1, "UNKNOWN": 0, "ALLOWED": 0}
    assert payload["skipped"] == []
    assert payload["errors"] == []

    entry = payload["results"][0]
    assert entry["status"] == "FAIL"
    assert entry["ecosystem"] == "npm"
    assert entry["name"] == "left-pad"
    assert entry["version"] == "1.3.0"
    assert entry["age_seconds"] == 10 * 3600
    assert entry["published"].endswith("Z")


def test_audit_json_reports_skips_and_errors(monkeypatch, capsys, tmp_path):
    install_fake_registry(monkeypatch, {("npm", "left-pad", "1.3.0"): 30 * 86400})
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "my-app"},
                    "node_modules/left-pad": {
                        "version": "1.3.0",
                        "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                    },
                    "node_modules/ui": {"resolved": "packages/ui", "link": True},
                },
            }
        ),
        encoding="utf-8",
    )
    code = cli.main(["audit", "--lock", str(lock), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"min_age_seconds", "summary", "results", "skipped", "errors"}
    assert payload["errors"] == []
    assert payload["skipped"] == [
        {"source": str(lock), "what": "node_modules/ui", "reason": "workspace link"}
    ]
    assert [r["name"] for r in payload["results"]] == ["left-pad"]


# ---------------------------------------------------------------- audit


def test_audit_allow_without_reason_exits(monkeypatch):
    install_fake_registry(monkeypatch, {})
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["audit", "--allow", "left-pad@1.3.0", "--allow-binary-lock"])
    assert "--allow requires --reason" in str(excinfo.value)


def test_audit_allow_with_reason_marks_the_version_allowed(monkeypatch, capsys, tmp_path):
    install_fake_registry(monkeypatch, {("npm", "left-pad", "1.3.0"): 10 * 3600})
    lock = tmp_path / "package-lock.json"
    lock.write_text(lockfile([("left-pad", "1.3.0")]), encoding="utf-8")

    code = cli.main([
        "audit", "--lock", str(lock),
        "--allow", "npm:left-pad@1.3.0",
        "--reason", "security hotfix, approved by AppSec",
        "--json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["status"] == "ALLOWED"
    assert payload["results"][0]["allow_reason"] == "security hotfix, approved by AppSec"


def test_audit_before_without_lock_exits(monkeypatch):
    install_fake_registry(monkeypatch, {})
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["audit", "--before", "old.json"])
    assert "--before requires --lock" in str(excinfo.value)


def test_audit_returns_1_for_a_young_new_version(monkeypatch, capsys, tmp_path):
    install_fake_registry(monkeypatch, {
        ("npm", "left-pad", "1.3.0"): 30 * 86400,
        ("npm", "chalk", "5.3.0"): 2 * 3600,
    })
    before = tmp_path / "before.json"
    before.write_text(lockfile([("left-pad", "1.3.0")]), encoding="utf-8")
    lock = tmp_path / "package-lock.json"
    lock.write_text(lockfile([("left-pad", "1.3.0"), ("chalk", "5.3.0")]), encoding="utf-8")

    code = cli.main(["audit", "--lock", str(lock), "--before", str(before)])
    assert code == 1
    out = capsys.readouterr().out
    assert "npm:chalk@5.3.0" in out
    assert "npm:left-pad@1.3.0" not in out   # unchanged, never looked up


def test_audit_binary_lock_fails_closed_and_the_flag_opens_it(monkeypatch, capsys, tmp_path):
    install_fake_registry(monkeypatch, {})
    lock = tmp_path / "bun.lockb"
    lock.write_bytes(b"\x00\x01binary")

    assert cli.main(["audit", "--lock", str(lock)]) == 1
    assert "binary lockfile" in capsys.readouterr().err

    assert cli.main(["audit", "--lock", str(lock), "--allow-binary-lock"]) == 0


def test_audit_bypass_env_turns_a_failure_into_success(monkeypatch, capsys, tmp_path):
    install_fake_registry(monkeypatch, {("npm", "chalk", "5.3.0"): 2 * 3600})
    lock = tmp_path / "package-lock.json"
    lock.write_text(lockfile([("chalk", "5.3.0")]), encoding="utf-8")

    assert cli.main(["audit", "--lock", str(lock)]) == 1
    monkeypatch.setenv(cli.BYPASS_ENV, "1")
    assert cli.main(["audit", "--lock", str(lock)]) == 0
    assert cli.BYPASS_ENV in capsys.readouterr().err


# ---------------------------------------------------------------- init-uv


def test_init_uv_dry_run_leaves_the_file_alone(tmp_path, capsys):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nname = "my-app"\n', encoding="utf-8")
    assert cli.main(["init-uv", str(tmp_path)]) == 0
    assert project.read_text(encoding="utf-8") == '[project]\nname = "my-app"\n'
    assert "dry run (insert)" in capsys.readouterr().out


def test_init_uv_write_applies_the_change(tmp_path, capsys):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nname = "my-app"\n', encoding="utf-8")
    assert cli.main(["init-uv", str(project), "--write"]) == 0
    assert 'exclude-newer = "72h"' in project.read_text(encoding="utf-8")


def test_init_uv_missing_pyproject_returns_1(tmp_path, capsys):
    assert cli.main(["init-uv", str(tmp_path)]) == 1
    assert "no pyproject.toml" in capsys.readouterr().err


# ---------------------------------------------------------------- report streams


def test_report_writes_to_the_current_stdout(monkeypatch):
    # Regression: a `stream=sys.stdout` default argument binds stdout at import
    # time, so a redirected stdout would never see the report.
    import io
    import sys

    from dep_age_gate import report
    from dep_age_gate.model import Dep

    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)

    dep = Dep("npm", "left-pad", "1.3.0", source="<cli>")
    result = Result(dep=dep, published=NOW, age_seconds=3600.0, status="PASS")
    report.render_table([result])
    report.render_json([result], 259200, [], [])

    text = buffer.getvalue()
    assert "npm:left-pad@1.3.0" in text
    assert '"min_age_seconds": 259200' in text
