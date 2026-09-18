"""Real local files and Git snapshots; registry resolution is not performed."""

import os
import ntpath
import stat
import subprocess
from types import SimpleNamespace

import pytest

from dep_age_gate import audit
from dep_age_gate.parsers import requirements
from dep_age_gate.requirements_source import RequirementsSource
from test_audit import git, keys, repo  # noqa: F401 - shared local Git fixture


def graph(tmp_path, files):
    for path, text in files.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    source = RequirementsSource(str(tmp_path))
    return requirements.parse(source.read("requirements.txt"), "requirements.txt", snapshot=source)


@pytest.mark.parametrize("directive", ["-r leaf", "-rleaf", "--requirement leaf", "--requirement=leaf"])
def test_include_forms(tmp_path, directive):
    result = graph(tmp_path, {"requirements.txt": directive, "leaf": "requests==2.32.3"})
    assert keys(result.deps) == {("pypi", "requests", "2.32.3")}
    assert result.deps[0].source == "leaf"
    assert result.errors == []


@pytest.mark.parametrize("directive", ["-c pins", "-cpins", "--constraint pins", "--constraint=pins"])
def test_constraints_only_select_named_requirements(tmp_path, directive):
    result = graph(tmp_path, {"requirements.txt": directive + "\nFoo_Bar\n",
                              "pins": "foo-bar==1.2\nunrelated==9.9\n"})
    assert keys(result.deps) == {("pypi", "Foo_Bar", "1.2")}
    assert result.errors == []


def test_nested_directive_kind_controls_requirement_or_constraint(tmp_path):
    result = graph(tmp_path, {"requirements.txt": "-c pins\n", "pins": "-r leaf\nunused==9.9",
                              "leaf": "requests==2.32.3"})
    assert keys(result.deps) == {("pypi", "requests", "2.32.3")}
    assert result.errors == []


@pytest.mark.parametrize("constraint", ["requests>=2", "requests[extra]==2.32.3",
                                        "requests==2.32.3; python_version>'3'", "requests==2.*"])
def test_unsupported_constraint_semantics_fail(tmp_path, constraint):
    result = graph(tmp_path, {"requirements.txt": "-c pins\nrequests", "pins": constraint})
    assert result.errors
    assert "unconditional exact pins" in result.errors[0]


@pytest.mark.parametrize("requirement", ["requests==1", "requests>=1", "requests @ https://example.invalid/a"])
def test_incompatible_or_unproven_constraints_fail(tmp_path, requirement):
    result = graph(tmp_path, {"requirements.txt": "-c pins\n" + requirement, "pins": "requests==2"})
    assert result.deps == []
    assert "compatible" in result.errors[0]


def test_conflicting_constraints_fail(tmp_path):
    result = graph(tmp_path, {"requirements.txt": "requests\n-c pins", "pins": "requests==1\nrequests==2"})
    assert result.deps == []
    assert "conflicting" in result.errors[0]


def test_windows_separators_quoted_paths_and_relative_parent(tmp_path):
    result = graph(tmp_path, {"requirements.txt": '-r "sub\\with space"\n',
                              "sub/with space": "-r ../leaf\n", "leaf": "requests==2.32.3"})
    assert keys(result.deps) == {("pypi", "requests", "2.32.3")}
    assert result.errors == []


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:\\secrets", "C:relative", "\\\\host\\share",
                                   "https://user:secret@example.invalid/pins", "file:///local",
                                   "${PRIVATE}/pins", "%PRIVATE%/pins", "a b", '"unterminated'])
def test_unsafe_paths_fail_without_open_or_network(tmp_path, monkeypatch, path):
    source = RequirementsSource(str(tmp_path))
    def fail_read(*args, **kwargs):
        pytest.fail("unsafe include reached source reader")
    monkeypatch.setattr(source, "read", fail_read)
    result = requirements.parse("-r " + path, "requirements.txt", snapshot=source)
    assert result.deps == []
    assert result.errors
    assert "secret" not in str(result.errors)


def test_repeated_diamond_is_not_a_cycle(tmp_path):
    result = graph(tmp_path, {"requirements.txt": "-r a\n-r b\n", "a": "-r leaf\n",
                              "b": "-r leaf\n", "leaf": "requests==2.32.3"})
    assert len(result.deps) == 1
    assert result.errors == []


def test_depth_limit(tmp_path):
    files = {"requirements.txt": "-r f0"}
    files.update({f"f{i}": f"-r f{i + 1}" for i in range(35)})
    result = graph(tmp_path, files)
    assert "depth limit" in str(result.errors)


def test_file_limit(tmp_path):
    files = {"requirements.txt": "\n".join(f"-r f{i}" for i in range(130))}
    files.update({f"f{i}": "requests==1" for i in range(130)})
    result = graph(tmp_path, files)
    assert "file count limit" in str(result.errors)


def test_size_limit(tmp_path):
    result = graph(tmp_path, {"requirements.txt": "-r leaf", "leaf": "#" * (1024 * 1024 + 1)})
    assert "file size limit" in str(result.errors)


def test_reparse_or_symlink_is_rejected_before_open(tmp_path, monkeypatch):
    source = RequirementsSource(str(tmp_path))
    monkeypatch.setattr(os, "lstat", lambda path: SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400))
    result = requirements.parse("-r link/pins", "requirements.txt", snapshot=source)
    assert "symlinks and reparse" in str(result.errors)


@pytest.mark.parametrize("mode", ["base", "staged", "explicit"])
def test_included_only_change_uses_matching_snapshot(repo, mode):
    (repo / "requirements.txt").write_text("-r deps.txt\n", encoding="utf-8")
    (repo / "deps.txt").write_text("requests==1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "include baseline")
    (repo / "deps.txt").write_text("requests==2\n", encoding="utf-8")
    options = {"cwd": str(repo), "base": "HEAD"}
    if mode == "staged":
        git(repo, "add", "deps.txt")
        (repo / "deps.txt").write_text("requests==3\n", encoding="utf-8")
        del options["base"]
    if mode == "explicit":
        options["paths"] = [str(repo / "requirements.txt")]
    targets, errors = audit.build_targets(**options)
    assert errors == []
    deps, skips, errors = audit.collect(targets)
    assert keys(deps) == {("pypi", "requests", "2")}
    assert skips == errors == []
    (repo / "deps.txt").write_text("requests==4\n", encoding="utf-8")
    git(repo, "add", "deps.txt")
    assert keys(audit.collect(targets)[0]) == {("pypi", "requests", "2")}


@pytest.mark.parametrize("mode", ["base", "staged"])
def test_deleted_include_is_not_filtered_out(repo, mode):
    (repo / "requirements.txt").write_text("-r deps.txt\n", encoding="utf-8")
    (repo / "deps.txt").write_text("requests==1", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "include baseline")
    git(repo, "rm", "deps.txt")
    targets, errors = audit.build_targets(cwd=str(repo), base="HEAD" if mode == "base" else None)
    assert errors == []
    assert len(targets) == 1
    assert "incomplete dependency coverage" in str(audit.collect(targets)[2])


def test_git_symlink_mode_is_rejected_even_if_worktree_is_regular(repo):
    (repo / "requirements.txt").write_text("-r link\n", encoding="utf-8")
    (repo / "link").write_text("requests==1", encoding="utf-8")
    git(repo, "add", ".")
    oid = subprocess.check_output(["git", "hash-object", "link"], cwd=repo, text=True).strip()
    git(repo, "update-index", "--cacheinfo", f"120000,{oid},link")
    targets, errors = audit.build_targets(cwd=str(repo))
    assert errors == []
    assert "non-regular" in str(audit.collect(targets)[2])


def test_supported_constraint_filename_is_not_an_install_root(repo):
    (repo / "requirements.txt").write_text("-c requirements-pins.txt\nrequests\n", encoding="utf-8")
    pins = repo / "requirements-pins.txt"
    pins.write_text("requests==1\nunrelated==1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "constraints baseline")
    pins.write_text("requests==2\nunrelated==2\n", encoding="utf-8")
    targets, errors = audit.build_targets(cwd=str(repo), base="HEAD")
    assert errors == []
    assert [target.label for target in targets] == ["requirements.txt"]
    deps, skips, errors = audit.collect(targets)
    assert keys(deps) == {("pypi", "requests", "2")}
    assert skips == errors == []


def test_total_size_limit(tmp_path):
    files = {"requirements.txt": "\n".join(f"-r f{i}" for i in range(9))}
    files.update({f"f{i}": "#" * (1024 * 1024) for i in range(9)})
    result = graph(tmp_path, files)
    assert "total size limit" in str(result.errors)


def test_before_file_never_uses_current_includes_as_history(tmp_path):
    (tmp_path / "requirements.txt").write_text("-r leaf", encoding="utf-8")
    (tmp_path / "before.txt").write_text("-r leaf", encoding="utf-8")
    (tmp_path / "leaf").write_text("requests==2", encoding="utf-8")
    targets, errors = audit.build_targets(lock=str(tmp_path / "requirements.txt"),
                                         before=str(tmp_path / "before.txt"))
    assert errors == []
    deps, skips, errors = audit.collect(targets)
    assert keys(deps) == {("pypi", "requests", "2")}
    assert skips == errors == []


def test_missing_historical_include_does_not_hide_new_current_pin(repo):
    (repo / "requirements.txt").write_text("-r leaf", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "missing historical include")
    (repo / "leaf").write_text("requests==2", encoding="utf-8")
    git(repo, "add", "leaf")
    targets, errors = audit.build_targets(cwd=str(repo), base="HEAD")
    assert errors == []
    deps, skips, errors = audit.collect(targets)
    assert keys(deps) == {("pypi", "requests", "2")}
    assert skips == errors == []


@pytest.mark.parametrize("line", ["requests==2 --index-url=https://example.invalid",
                                  "requests==2 --unknown", "./local-project"])
def test_unsupported_inline_options_or_paths_do_not_disappear(tmp_path, line):
    result = graph(tmp_path, {"requirements.txt": "-r leaf", "leaf": line})
    assert result.deps == []
    assert "incomplete dependency coverage" in str(result.errors)


def test_wildcard_is_not_an_exact_pin(tmp_path):
    result = graph(tmp_path, {"requirements.txt": "-r leaf", "leaf": "requests==2.*"})
    assert result.deps == []
    assert len(result.skips) == 1


def test_bad_git_base_fails_instead_of_falling_back_to_filesystem(repo):
    (repo / "requirements.txt").write_text("requests==2", encoding="utf-8")
    targets, errors = audit.build_targets(paths=[str(repo / "requirements.txt")],
                                         base="missing-ref", cwd=str(repo))
    assert targets == []
    assert "Git source snapshot" in str(errors)


@pytest.mark.parametrize("historical_include", ["-r missing", "--index-url https://example.invalid/simple"])
def test_incomplete_history_cannot_suppress_current_pin_age_check(repo, historical_include):
    path = repo / "requirements.txt"
    path.write_text(historical_include + "\nrequests==2\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "untrusted historical source")
    path.write_text("requests==2\n", encoding="utf-8")
    targets, errors = audit.build_targets(cwd=str(repo), base="HEAD")
    assert errors == []
    deps, skips, errors = audit.collect(targets)
    assert keys(deps) == {("pypi", "requests", "2")}
    assert skips == errors == []


def test_cross_drive_paths_are_not_compared_to_unrelated_repository(monkeypatch):
    monkeypatch.setattr(audit.os.path, "abspath", ntpath.abspath)
    monkeypatch.setattr(audit.os.path, "relpath", ntpath.relpath)
    assert audit._relative_inside(r"C:\project\requirements.txt", r"D:\repository") is None


@pytest.mark.parametrize("mode", ["lock", "explicit"])
def test_outside_repository_input_uses_its_own_containing_root(repo, tmp_path, mode):
    outside = tmp_path / "outside"
    outside.mkdir()
    path = outside / "requirements.txt"
    path.write_text("-r leaf", encoding="utf-8")
    (outside / "leaf").write_text("requests==2", encoding="utf-8")
    options = {"lock": str(path)} if mode == "lock" else {"paths": [str(path)]}
    targets, errors = audit.build_targets(cwd=str(repo), **options)
    assert errors == []
    deps, skips, errors = audit.collect(targets)
    assert keys(deps) == {("pypi", "requests", "2")}
    assert deps[0].source == "leaf"
    assert skips == errors == []


def test_text_only_partial_history_cannot_suppress_current_pin():
    target = audit.Target("requirements.txt", "requests==2", "-r missing\nrequests==2")
    deps, skips, errors = audit.collect([target])
    assert keys(deps) == {("pypi", "requests", "2")}
    assert skips == errors == []
