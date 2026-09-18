"""audit.build_targets + audit.collect: only new versions must come back."""

import json
import os
import shutil
import subprocess

import pytest

from dep_age_gate import audit

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")


def lockfile(entries):
    """A package-lock.json v3 with one node_modules entry per (name, version)."""
    packages = {"": {"name": "my-app", "version": "1.0.0"}}
    for name, version in entries:
        packages[f"node_modules/{name}"] = {
            "version": version,
            "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz",
            "integrity": "sha512-" + "0" * 86 + "==",
        }
    return json.dumps(
        {"name": "my-app", "version": "1.0.0", "lockfileVersion": 3, "packages": packages},
        indent=2,
    ) + "\n"


def git(repo, *args):
    subprocess.run(
        [GIT, "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"] + list(args),
        cwd=str(repo), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


@pytest.fixture()
def repo(tmp_path):
    """A git repo whose HEAD holds left-pad@1.3.0 and lodash@4.17.21."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "package-lock.json").write_text(
        lockfile([("left-pad", "1.3.0"), ("lodash", "4.17.21")]), encoding="utf-8"
    )
    git(root, "add", "package-lock.json")
    git(root, "commit", "-q", "-m", "initial lockfile")
    return root


def keys(deps):
    return {dep.key for dep in deps}


@pytest.mark.parametrize("layout", ["nested", "cycle", "missing", "relative", "url"])
def test_requirements_includes_are_rejected_without_reading_them(tmp_path, monkeypatch, layout):
    import builtins
    import urllib.request

    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "deps.txt").write_text("-r ../leaf.txt\n", encoding="utf-8")
    (tmp_path / "leaf.txt").write_text("requests==2.32.3\n", encoding="utf-8")
    include = {
        "nested": "sub/deps.txt", "cycle": "requirements.txt",
        "missing": "absent.txt", "relative": "sub/../leaf.txt",
        "url": "https://example.invalid/deps.txt",
    }[layout]
    path = tmp_path / "requirements.txt"
    path.write_text(f"-r {include}\n", encoding="utf-8")
    targets, errors = audit.build_targets(lock=str(path))
    assert errors == []

    def unexpected_read(*args, **kwargs):
        pytest.fail("requirements parsing must not open includes or fetch URLs")

    monkeypatch.setattr(builtins, "open", unexpected_read)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected_read)
    deps, skips, errors = audit.collect(targets)
    assert deps == []
    assert skips == []
    assert len(errors) == 1
    assert "unsupported requirements include" in errors[0]


@needs_git
@pytest.mark.parametrize("mode", ["staged", "base", "paths"])
def test_requirements_diff_reports_unchanged_include_and_only_new_pins(repo, mode):
    path = repo / "requirements.txt"
    path.write_text("-r deps.txt\nrequests==2.32.3\n", encoding="utf-8")
    git(repo, "add", "requirements.txt")
    git(repo, "commit", "-qm", "requirements baseline")
    path.write_text("-r deps.txt\nrequests==2.32.3\ncertifi==2024.8.30\n", encoding="utf-8")
    options = {"cwd": str(repo)}
    if mode == "staged":
        git(repo, "add", "requirements.txt")
        # Reading the working tree would incorrectly lose the include error.
        path.write_text("requests==2.32.3\n", encoding="utf-8")
    elif mode == "base":
        options["base"] = "HEAD"
    else:
        options["paths"] = [str(path)]
    targets, errors = audit.build_targets(**options)
    assert errors == []
    deps, _, errors = audit.collect(targets)
    assert keys(deps) == {("pypi", "certifi", "2024.8.30")}
    assert len(errors) == 1
    assert "incomplete dependency coverage" in errors[0]


def test_requirements_removed_include_does_not_fail_current_snapshot():
    target = audit.Target("requirements.txt", "requests==2.32.3\n",
                          "-r deps.txt\n")
    deps, _, errors = audit.collect([target])
    assert keys(deps) == {("pypi", "requests", "2.32.3")}
    assert errors == []


# ---------------------------------------------------------------- staged diff


@needs_git
def test_staged_change_reports_only_the_newly_added_version(repo):
    (repo / "package-lock.json").write_text(
        lockfile([("left-pad", "1.3.0"), ("lodash", "4.17.21"), ("chalk", "5.3.0")]),
        encoding="utf-8",
    )
    git(repo, "add", "package-lock.json")

    targets, errors = audit.build_targets(cwd=str(repo))
    assert errors == []
    assert [t.label for t in targets] == ["package-lock.json"]

    deps, skips, parse_errors = audit.collect(targets)
    assert parse_errors == []
    assert keys(deps) == {("npm", "chalk", "5.3.0")}


@needs_git
def test_a_bumped_version_is_new_and_the_old_one_is_gone(repo):
    (repo / "package-lock.json").write_text(
        lockfile([("left-pad", "1.3.0"), ("lodash", "4.17.22")]), encoding="utf-8"
    )
    git(repo, "add", "package-lock.json")

    targets, _ = audit.build_targets(cwd=str(repo))
    deps, _, _ = audit.collect(targets)
    assert keys(deps) == {("npm", "lodash", "4.17.22")}


@needs_git
def test_no_staged_change_means_no_targets(repo):
    targets, errors = audit.build_targets(cwd=str(repo))
    assert targets == []
    assert errors == []


@needs_git
def test_scan_all_returns_every_version_not_just_the_new_one(repo):
    (repo / "package-lock.json").write_text(
        lockfile([("left-pad", "1.3.0"), ("lodash", "4.17.21"), ("chalk", "5.3.0")]),
        encoding="utf-8",
    )
    git(repo, "add", "package-lock.json")

    targets, errors = audit.build_targets(
        paths=[str(repo / "package-lock.json")], scan_all=True, cwd=str(repo)
    )
    assert errors == []
    deps, _, _ = audit.collect(targets)
    assert keys(deps) == {
        ("npm", "left-pad", "1.3.0"),
        ("npm", "lodash", "4.17.21"),
        ("npm", "chalk", "5.3.0"),
    }


@needs_git
def test_explicit_path_without_scan_all_still_diffs_against_head(repo):
    (repo / "package-lock.json").write_text(
        lockfile([("left-pad", "1.3.0"), ("lodash", "4.17.21"), ("chalk", "5.3.0")]),
        encoding="utf-8",
    )
    targets, errors = audit.build_targets(
        paths=[str(repo / "package-lock.json")], cwd=str(repo)
    )
    assert errors == []
    deps, _, _ = audit.collect(targets)
    assert keys(deps) == {("npm", "chalk", "5.3.0")}


@needs_git
def test_base_ref_compares_the_working_tree_against_that_ref(repo):
    (repo / "package-lock.json").write_text(
        lockfile([("left-pad", "1.3.0"), ("lodash", "4.17.21"), ("chalk", "5.3.0")]),
        encoding="utf-8",
    )
    git(repo, "add", "package-lock.json")
    git(repo, "commit", "-q", "-m", "add chalk")

    targets, errors = audit.build_targets(base="HEAD~1", cwd=str(repo))
    assert errors == []
    deps, _, _ = audit.collect(targets)
    assert keys(deps) == {("npm", "chalk", "5.3.0")}


@needs_git
def test_discover_walks_a_directory_and_ignores_node_modules(repo):
    nested = repo / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "package-lock.json").write_text(lockfile([("semver", "7.6.3")]), encoding="utf-8")
    vendored = repo / "node_modules" / "left-pad"
    vendored.mkdir(parents=True)
    (vendored / "package-lock.json").write_text(lockfile([("nope", "1.0.0")]), encoding="utf-8")

    found = audit.discover([str(repo)])
    names = {os.path.relpath(path, str(repo)) for path in found}
    assert names == {"package-lock.json", os.path.join("packages", "api", "package-lock.json")}


# ---------------------------------------------------------------- --lock/--before


def test_lock_and_before_two_plain_files_without_git(tmp_path):
    before = tmp_path / "before.json"
    before.write_text(lockfile([("left-pad", "1.3.0"), ("lodash", "4.17.21")]), encoding="utf-8")
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        lockfile([("left-pad", "1.3.0"), ("lodash", "4.17.21"), ("chalk", "5.3.0")]),
        encoding="utf-8",
    )

    targets, errors = audit.build_targets(lock=str(lock), before=str(before))
    assert errors == []
    assert len(targets) == 1
    assert targets[0].base is not None

    deps, skips, parse_errors = audit.collect(targets)
    assert parse_errors == []
    assert keys(deps) == {("npm", "chalk", "5.3.0")}


def test_lock_with_a_missing_before_snapshot_treats_everything_as_new(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(lockfile([("left-pad", "1.3.0"), ("chalk", "5.3.0")]), encoding="utf-8")

    targets, errors = audit.build_targets(lock=str(lock), before=str(tmp_path / "gone.json"))
    assert errors == []
    assert targets[0].base is None
    deps, _, _ = audit.collect(targets)
    assert keys(deps) == {("npm", "left-pad", "1.3.0"), ("npm", "chalk", "5.3.0")}


def test_unreadable_lock_is_an_error(tmp_path):
    targets, errors = audit.build_targets(lock=str(tmp_path / "nope.json"))
    assert targets == []
    assert errors == [f"{tmp_path / 'nope.json'}: cannot read"]


# ---------------------------------------------------------------- collect


def test_collect_reports_skips_and_errors_from_the_current_file(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "my-app"},
                    "node_modules/left-pad": {"version": "1.3.0"},
                    "node_modules/ui": {"resolved": "packages/ui", "link": True},
                },
            }
        ),
        encoding="utf-8",
    )
    targets, _ = audit.build_targets(lock=str(lock))
    deps, skips, errors = audit.collect(targets)
    assert keys(deps) == {("npm", "left-pad", "1.3.0")}
    assert [s.reason for s in skips] == ["workspace link"]
    assert errors == []


def test_collect_reports_an_unroutable_label_as_an_error():
    target = audit.Target("Gemfile.lock", "irrelevant")
    deps, skips, errors = audit.collect([target])
    assert deps == []
    assert errors == ["Gemfile.lock: no parser for this filename"]


def test_collect_fails_closed_on_a_binary_bun_lock(tmp_path):
    lock = tmp_path / "bun.lockb"
    lock.write_bytes(b"\x00\x01binary")
    targets, _ = audit.build_targets(lock=str(lock))
    deps, skips, errors = audit.collect(targets)
    assert deps == []
    assert len(errors) == 1
    assert "binary lockfile" in errors[0]

    deps, skips, errors = audit.collect(targets, allow_binary_lock=True)
    assert errors == []
    assert len(skips) == 1


# --- .dep-age-gate-ignore -------------------------------------------------

def test_ignore_file_skips_matching_lockfiles(tmp_path):
    from dep_age_gate import audit

    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    fixture = tmp_path / "tests" / "fixtures" / "uv.lock"
    fixture.write_text(
        'version = 1\n\n[[package]]\nname = "x"\nversion = "1.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n',
        encoding="utf-8",
    )
    real = tmp_path / "uv.lock"
    real.write_text(
        'version = 1\n\n[[package]]\nname = "y"\nversion = "2.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n',
        encoding="utf-8",
    )
    (tmp_path / ".dep-age-gate-ignore").write_text(
        "# comment\ntests/fixtures/**\n", encoding="utf-8"
    )

    patterns = audit.load_ignores(str(tmp_path))
    assert patterns == ["tests/fixtures/**"]
    assert audit.is_ignored(str(fixture), patterns, str(tmp_path)) is True
    assert audit.is_ignored(str(real), patterns, str(tmp_path)) is False


def test_exclude_flag_adds_patterns(tmp_path):
    from dep_age_gate import audit

    patterns = audit.load_ignores(str(tmp_path), extra=["vendor/**", "*.bak.lock"])
    assert audit.is_ignored("vendor/uv.lock", patterns, str(tmp_path)) is True
    assert audit.is_ignored("old.bak.lock", patterns, str(tmp_path)) is True
    assert audit.is_ignored("uv.lock", patterns, str(tmp_path)) is False
