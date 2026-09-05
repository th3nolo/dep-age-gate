"""Thin git helpers. Everything is optional: with no git, audit scans whole files."""

import os
import subprocess


class GitError(Exception):
    pass


def _run(args, cwd=None, binary=False):
    process = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        raise GitError(process.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} failed")
    return process.stdout if binary else process.stdout.decode("utf-8", "replace")


def repo_root(path="."):
    start = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path)) or "."
    try:
        return _run(["rev-parse", "--show-toplevel"], cwd=start).strip()
    except GitError:
        return None


def has_head(root):
    try:
        _run(["rev-parse", "--verify", "-q", "HEAD"], cwd=root)
        return True
    except GitError:
        return False


def show(root, ref, relpath, binary=False):
    """Content of `relpath` at `ref`. None when the path does not exist there.

    `ref` may be the empty string, which reads the staging area (git show :path).
    """
    spec = f"{ref}:{relpath}" if ref else f":{relpath}"
    try:
        return _run(["show", spec], cwd=root, binary=binary)
    except GitError:
        return None


def staged_paths(root):
    out = _run(["diff", "--cached", "--name-only", "--diff-filter=ACMR"], cwd=root)
    return [line.strip() for line in out.splitlines() if line.strip()]


def changed_paths(root, base):
    out = _run(["diff", "--name-only", "--diff-filter=ACMR", base], cwd=root)
    return [line.strip() for line in out.splitlines() if line.strip()]


def tracked_paths(root):
    out = _run(["ls-files"], cwd=root)
    return [line.strip() for line in out.splitlines() if line.strip()]
