"""Duration parsing.

Accepts three spellings, all returning seconds:

  short   72h, 3d, 4320m, 259200s, 1w
  ISO8601 PT72H, P3D, P1W
  words   "72 hours", "3 days"

This is the tool's own unit handling only. Each package manager has its own
unit and its own key; see README.md for the table.
"""

import re

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}

_SHORT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")
_ISO = re.compile(
    r"^P(?:(?P<w>\d+(?:\.\d+)?)W)?(?:(?P<d>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)


class DurationError(ValueError):
    pass


def parse_duration(text: str) -> int:
    """Return seconds. Raise DurationError on anything not understood.

    A bare number is seconds. Never guess a different unit: silently reading
    the wrong unit is the exact failure this tool exists to prevent.
    """
    if text is None:
        raise DurationError("empty duration")
    s = str(text).strip()
    if not s:
        raise DurationError("empty duration")

    up = s.upper()
    if up.startswith("P") and up != "P":
        m = _ISO.match(up)
        if m and any(m.group(g) for g in ("w", "d", "h", "m", "s")):
            total = 0.0
            total += float(m.group("w") or 0) * 604800
            total += float(m.group("d") or 0) * 86400
            total += float(m.group("h") or 0) * 3600
            total += float(m.group("m") or 0) * 60
            total += float(m.group("s") or 0)
            return int(total)
        raise DurationError(f"not a valid ISO-8601 duration: {text!r}")

    m = _SHORT.match(s)
    if not m:
        raise DurationError(
            f"cannot parse duration {text!r}; use e.g. 72h, 3d, PT72H, '72 hours'"
        )
    value, unit = m.group(1), m.group(2).lower()
    if not unit:
        return int(float(value))
    if unit not in _UNITS:
        raise DurationError(f"unknown duration unit {unit!r} in {text!r}")
    return int(float(value) * _UNITS[unit])


def humanize(seconds: float) -> str:
    """Compact age string: 4d5h, 13h2m, 44m."""
    seconds = int(max(0, seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    mnt = rem // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{mnt}m"
    return f"{mnt}m"
