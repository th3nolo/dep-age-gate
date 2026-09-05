"""parse_duration: every accepted spelling, and the rejections."""

import pytest

from dep_age_gate.duration import DurationError, humanize, parse_duration

THREE_DAYS = 259200


@pytest.mark.parametrize(
    "text",
    ["72h", "3d", "4320m", "259200s", "PT72H", "P3D", "72 hours", "259200"],
)
def test_three_days_in_every_spelling(text):
    assert parse_duration(text) == THREE_DAYS


def test_week_units():
    assert parse_duration("1w") == 604800
    assert parse_duration("P1W") == 604800
    assert parse_duration("1 week") == 604800


def test_bare_number_is_seconds():
    # Never guess a different unit: a bare 72 is 72 seconds, not 72 hours.
    assert parse_duration("72") == 72


def test_long_unit_names_and_spacing():
    assert parse_duration("  3 days  ") == THREE_DAYS
    assert parse_duration("90 MINUTES") == 5400
    assert parse_duration("1.5h") == 5400


def test_iso_combined():
    assert parse_duration("P1DT12H") == 129600


@pytest.mark.parametrize("text", ["", "   ", "abc", "72x", "P", "PT", "h", "-3d", None])
def test_garbage_raises(text):
    with pytest.raises(DurationError):
        parse_duration(text)


def test_humanize():
    assert humanize(THREE_DAYS) == "3d0h"
    assert humanize(3600 * 13 + 120) == "13h2m"
    assert humanize(60 * 44) == "44m"
    assert humanize(-5) == "0m"
