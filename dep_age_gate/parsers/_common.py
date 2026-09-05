"""Shared helpers for the lockfile parsers."""

import re

# Version strings that are not a registry release.
_NON_REGISTRY_PREFIXES = (
    "file:", "link:", "portal:", "workspace:", "patch:", "git:", "git+",
    "http:", "https:", "npm:", "github:", "gitlab:", "bitbucket:", "exec:",
)


def looks_like_registry_version(version: str) -> bool:
    """True when `version` is a plain release number, not a URL or path spec."""
    if not version:
        return False
    low = version.lower()
    for prefix in _NON_REGISTRY_PREFIXES:
        if low.startswith(prefix):
            return False
    if "://" in version or version.startswith("/") or version.startswith("."):
        return False
    # A release number always starts with a digit or a `v`.
    return bool(re.match(r"^v?\d", version))


def split_name_version(text: str):
    """Split `name@version` / `@scope/name@version`. Returns (name, version)."""
    at = text.rfind("@")
    if at <= 0:
        return None, None
    return text[:at], text[at + 1:]


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas.

    bun.lock is JSON with trailing commas; some hand-edited lockfiles add
    comments. json.loads rejects both.
    """
    out = []
    i = 0
    length = len(text)
    in_string = False
    while i < length:
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\" and i + 1 < length:
                out.append(text[i + 1])
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < length and text[i + 1] == "/":
            while i < length and text[i] != "\n":
                i += 1
            continue
        if char == "/" and i + 1 < length and text[i + 1] == "*":
            i += 2
            while i + 1 < length and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(char)
        i += 1
    stripped = "".join(out)
    # Trailing commas before } or ]
    return re.sub(r",(\s*[}\]])", r"\1", stripped)


_TABLE_HEADER = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*$")


def iter_toml_package_blocks(text: str):
    """Yield the raw text of each `[[package]]` block.

    A block runs to the next `[[package]]` header, so poetry's `[package.source]`
    sub-table stays with the package it belongs to.

    This is a deliberate line scanner rather than tomllib: tomllib only exists
    on Python 3.11+, and every lockfile shape we read here (`uv.lock`,
    `Cargo.lock`, `poetry.lock`) uses plain quoted scalars in `[[package]]`
    arrays of tables.
    """
    current = None
    for line in text.splitlines():
        match = _TABLE_HEADER.match(line)
        if match:
            name = match.group(1).strip()
            is_array = line.strip().startswith("[[")
            if name == "package" and is_array:
                if current is not None:
                    yield "\n".join(current)
                current = [line]
                continue
            if not name.startswith("package."):
                if current is not None:
                    yield "\n".join(current)
                current = None
                continue
        if current is not None:
            current.append(line)
    if current is not None:
        yield "\n".join(current)


def toml_string(block: str, key: str):
    """Read a top-level `key = "value"` out of a block."""
    match = re.search(
        r'^\s*%s\s*=\s*"([^"]*)"\s*$' % re.escape(key), block, re.MULTILINE
    )
    if match:
        return match.group(1)
    match = re.search(
        r"^\s*%s\s*=\s*'([^']*)'\s*$" % re.escape(key), block, re.MULTILINE
    )
    return match.group(1) if match else None
