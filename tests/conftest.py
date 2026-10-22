"""Make the repo root importable so tests run without installing the package."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture(scope="session")
def empty_gitconfig(tmp_path_factory):
    path = tmp_path_factory.mktemp("git") / "empty-gitconfig"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch, empty_gitconfig):
    """Temporary test repos must not inherit the developer's global git config.

    A global core.hooksPath (for example this tool's own pre-commit hook) would
    otherwise run inside fixture commits and make them fail. The file lives
    outside each test's tmp_path, which some tests require to stay empty.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def fixture_text(name):
    """Read one fixture file as text."""
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return handle.read()
