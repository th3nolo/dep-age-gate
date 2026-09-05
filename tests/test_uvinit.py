"""uvinit.plan: where exclude-newer lands and what action is reported."""

import re

from dep_age_gate import uvinit

NO_TOOL_UV = """\
[project]
name = "my-app"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []
"""

EXISTING_SECTION_NO_KEY = """\
[project]
name = "my-app"
version = "0.1.0"

[tool.uv]
managed = true
package = false

[tool.ruff]
line-length = 100
"""

EXISTING_DIFFERENT_VALUE = """\
[project]
name = "my-app"

[tool.uv]
exclude-newer = "24h"
managed = true

[tool.ruff]
line-length = 100
"""

EXISTING_SAME_VALUE = """\
[project]
name = "my-app"

[tool.uv]
exclude-newer = "72h"
managed = true
"""


def section_bounds(text, header="[tool.uv]"):
    """Return (first line index inside the section, first line index after it)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == header)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^\s*\[", lines[index]):
            end = index
            break
    return lines, start + 1, end


def key_line(lines):
    return next(i for i, line in enumerate(lines) if line.startswith("exclude-newer"))


def test_insert_when_there_is_no_tool_uv_section():
    new, action = uvinit.plan(NO_TOOL_UV)
    assert action == "insert"
    assert '[tool.uv]' in new
    assert 'exclude-newer = "72h"' in new

    lines, start, end = section_bounds(new)
    assert start <= key_line(lines) < end
    assert new.startswith(NO_TOOL_UV)          # nothing above is disturbed
    assert new.endswith('exclude-newer = "72h"\n')


def test_insert_uses_the_span_it_is_given():
    new, action = uvinit.plan(NO_TOOL_UV, span="7d")
    assert action == "insert"
    assert 'exclude-newer = "7d"' in new
    lines, start, end = section_bounds(new)
    assert start <= key_line(lines) < end


def test_insert_into_an_existing_section_stays_inside_that_section():
    new, action = uvinit.plan(EXISTING_SECTION_NO_KEY)
    assert action == "insert"

    lines, start, end = section_bounds(new)
    index = key_line(lines)
    assert start <= index < end                      # inside [tool.uv]
    assert lines[end].strip() == "[tool.ruff]"       # before the next header
    assert "managed = true" in new                   # existing keys survive
    assert "line-length = 100" in new


def test_update_replaces_a_different_value():
    new, action = uvinit.plan(EXISTING_DIFFERENT_VALUE)
    assert action == "update"
    assert 'exclude-newer = "72h"' in new
    assert 'exclude-newer = "24h"' not in new
    assert new.count("exclude-newer") == 1
    assert "managed = true" in new
    assert "[tool.ruff]" in new


def test_noop_when_the_value_already_matches():
    new, action = uvinit.plan(EXISTING_SAME_VALUE)
    assert action == "noop"
    assert new == EXISTING_SAME_VALUE


def test_odd_spacing_is_rewritten_to_the_canonical_line():
    # Only a byte-identical line is a noop; anything else is normalised.
    text = EXISTING_SAME_VALUE.replace(
        'exclude-newer = "72h"', 'exclude-newer   =   "72h"  '
    )
    result, action = uvinit.plan(text)
    assert action == "update"
    assert 'exclude-newer = "72h"\n' in result
    assert result.count("exclude-newer") == 1


def test_plan_on_an_empty_file_still_produces_a_valid_section():
    new, action = uvinit.plan("")
    assert action == "insert"
    lines, start, end = section_bounds(new)
    assert start <= key_line(lines) < end


def test_diff_is_a_unified_diff_naming_the_file():
    old = NO_TOOL_UV
    new, _ = uvinit.plan(old)
    text = uvinit.diff("pyproject.toml", old, new)
    assert "--- a/pyproject.toml" in text
    assert "+++ b/pyproject.toml" in text
    assert '+exclude-newer = "72h"' in text
