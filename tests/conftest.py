"""Make the repo root importable so tests run without installing the package."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture_text(name):
    """Read one fixture file as text."""
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return handle.read()
