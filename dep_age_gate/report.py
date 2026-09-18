"""Console table and JSON output."""

import hashlib
import json
import os
import sys

from dep_age_gate.duration import humanize

_ORDER = {"FAIL": 0, "UNKNOWN": 1, "ALLOWED": 2, "PASS": 3}


def _registry_id(dep):
    return hashlib.sha256(dep.registry.encode()).hexdigest()[:16] if dep.registry else None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else "-"


def _short_source(source):
    """Show a path relative to the working directory when that is shorter."""
    if not source:
        return "-"
    if not os.path.isabs(source):
        return source
    try:
        relative = os.path.relpath(source, os.getcwd())
    except ValueError:
        return source
    return relative if len(relative) < len(source) and not relative.startswith("../..") else source


def _rows(results):
    rows = []
    for result in results:
        rows.append(
            [
                result.status,
                result.dep.label() + (f" [index:{_registry_id(result.dep)}]" if result.dep.registry else ""),
                _iso(result.published),
                humanize(result.age_seconds) if result.age_seconds is not None else "-",
                _short_source(result.dep.source),
                result.allow_reason or result.message or ("configured Python index" if result.dep.registry else ""),
            ]
        )
    return rows


def render_table(results, stream=None, show_pass=True):
    # Resolved here, not in the default argument: a default binds sys.stdout
    # at import time and would keep writing to it after a caller redirected it.
    stream = sys.stdout if stream is None else stream
    shown = [r for r in results if show_pass or r.status != "PASS"]
    shown.sort(key=lambda r: (_ORDER.get(r.status, 9), r.dep.label()))
    if not shown:
        return
    header = ["STATUS", "PACKAGE", "PUBLISHED (UTC)", "AGE", "SOURCE", "NOTE"]
    rows = [header] + _rows(shown)
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(header))]
    for index, row in enumerate(rows):
        line = "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        stream.write(line + "\n")
        if index == 0:
            stream.write("  ".join("-" * width for width in widths) + "\n")


def summarize(results):
    counts = {"PASS": 0, "FAIL": 0, "UNKNOWN": 0, "ALLOWED": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def render_json(results, min_age_seconds, skips, errors, stream=None):
    stream = sys.stdout if stream is None else stream
    payload = {
        "min_age_seconds": min_age_seconds,
        "summary": summarize(results),
        "results": [
            {
                "status": r.status,
                "ecosystem": r.dep.ecosystem,
                "name": r.dep.name,
                "version": r.dep.version,
                "published": _iso(r.published) if r.published else None,
                "age_seconds": int(r.age_seconds) if r.age_seconds is not None else None,
                "source": r.dep.source,
                "detail": r.dep.detail,
                "registry_id": _registry_id(r.dep),
                "allow_reason": r.allow_reason or None,
                "message": r.message or None,
            }
            for r in results
        ],
        "skipped": [
            {"source": s.source, "what": s.what, "reason": s.reason} for s in skips
        ],
        "errors": list(errors),
    }
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
