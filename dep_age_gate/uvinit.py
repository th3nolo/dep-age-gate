"""Add uv's release-age gate to a project's pyproject.toml.

The key is `exclude-newer` under `[tool.uv]` and it accepts a relative span,
so `exclude-newer = "72h"` means "72 hours before now", re-evaluated whenever
uv resolves. uv writes the resolved span into uv.lock as
`exclude-newer-span = "PT72H"`.

This is deliberately per project, never global (~/.config/uv/uv.toml):
`exclude-newer` changes resolution results and is recorded in every uv.lock
uv writes. A global value would silently rewrite the lockfiles of every repo
on the machine, including repos owned by other people, and the change would
not be visible in that repo's own configuration.
"""

import difflib
import re

MARKER = "exclude-newer"
DEFAULT_SPAN = "72h"

_COMMENT = (
    "# Supply-chain gate: refuse PyPI releases younger than {span}.\n"
    "# Relative span, re-evaluated on every resolve; uv records it in uv.lock\n"
    "# as exclude-newer-span. Docs: https://docs.astral.sh/uv/reference/settings/#exclude-newer\n"
)


def plan(text, span=DEFAULT_SPAN):
    """Return (new_text, action) where action is 'noop', 'update' or 'insert'."""
    lines = text.splitlines(keepends=True)
    section = None
    for index, line in enumerate(lines):
        if re.match(r"^\s*\[tool\.uv\]\s*$", line):
            section = index
            break

    if section is None:
        suffix = "" if text.endswith("\n") or not text else "\n"
        block = suffix + "\n[tool.uv]\n" + _COMMENT.format(span=span)
        block += f'{MARKER} = "{span}"\n'
        return text + block, "insert"

    end = len(lines)
    for index in range(section + 1, len(lines)):
        if re.match(r"^\s*\[", lines[index]):
            end = index
            break

    for index in range(section + 1, end):
        if re.match(r"^\s*%s\s*=" % re.escape(MARKER), lines[index]):
            current = lines[index]
            wanted = f'{MARKER} = "{span}"\n'
            if current.strip() == wanted.strip():
                return text, "noop"
            lines[index] = wanted
            return "".join(lines), "update"

    insert_at = section + 1
    lines.insert(insert_at, _COMMENT.format(span=span) + f'{MARKER} = "{span}"\n')
    return "".join(lines), "insert"


def diff(path, old, new):
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
