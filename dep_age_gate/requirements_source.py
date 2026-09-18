"""Bounded, local requirements inputs from one filesystem or Git snapshot."""

import ntpath
import os
import posixpath
import stat

from dep_age_gate import gitutil

MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * MAX_FILE_BYTES
MAX_FILES = 128
MAX_DEPTH = 32


class SourceError(ValueError):
    """An input cannot safely be represented in this snapshot."""


def include_path(parent: str, argument: str) -> str:
    """Accept portable relative paths; never interpret URLs or host variables."""
    if (not argument or any(c in argument for c in ("\x00", "$", "%", ":"))
            or ntpath.isabs(argument) or ntpath.splitdrive(argument)[0]):
        raise SourceError("include must be a local relative path")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(parent),
                                           argument.replace("\\", "/")))
    if path == ".." or path.startswith("../") or path.startswith("/"):
        raise SourceError("include escapes the source root")
    return path


class RequirementsSource:
    """Cache reads immediately; Git sources pin blob IDs including index entries."""

    def __init__(self, root: str, ref: str | None = None,
                 entries: dict[str, tuple[str, str]] | None = None):
        self.root = os.path.abspath(root)
        self.ref = ref
        self.entries: dict[str, tuple[str, str]] | None = None
        self.cache: dict[str, str] = {}
        self.total = 0
        if ref is not None:
            self.entries = entries if entries is not None else gitutil.snapshot_entries(root, ref)

    def read(self, path: str) -> str:
        if path in self.cache:
            return self.cache[path]
        if len(self.cache) >= MAX_FILES:
            raise SourceError("requirements file count limit exceeded")
        try:
            if self.entries is not None:
                mode, oid = self.entries.get(path, ("missing", ""))
                if mode not in ("100644", "100755"):
                    raise SourceError("missing or non-regular requirements file in Git snapshot")
                try:
                    data = gitutil.read_bounded_blob(self.root, oid, MAX_FILE_BYTES)
                except ValueError as exc:
                    raise SourceError(str(exc)) from exc
            else:
                full = self.root
                root_info = os.lstat(full)
                if stat.S_ISLNK(root_info.st_mode) or getattr(root_info, "st_file_attributes", 0) & 0x400:
                    raise SourceError("symlinks and reparse points are not supported")
                for component in path.split("/"):
                    if component in ("", ".", ".."):
                        raise SourceError("invalid requirements source path")
                    full = os.path.join(full, component)
                    info = os.lstat(full)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise SourceError("symlinks and reparse points are not supported")
                if not stat.S_ISREG(info.st_mode):
                    raise SourceError("requirements input is not a regular file")
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(full, flags), "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
                        raise SourceError("requirements input changed while opening")
                    data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise SourceError("requirements file size limit exceeded")
            self.total += len(data)
            if self.total > MAX_TOTAL_BYTES:
                raise SourceError("requirements total size limit exceeded")
            text = data.decode("utf-8-sig")
        except (OSError, UnicodeError, gitutil.GitError) as exc:
            raise SourceError("cannot read requirements file from source snapshot") from exc
        self.cache[path] = text
        return text
