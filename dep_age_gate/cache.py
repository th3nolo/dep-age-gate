"""On-disk cache for registry answers.

Linux/macOS : $XDG_CACHE_HOME/dep-age-gate  (default ~/.cache/dep-age-gate)
Windows     : %LOCALAPPDATA%\\dep-age-gate\\cache
Override    : $DEP_AGE_GATE_CACHE

One JSON file per cache key. Default TTL 24 h. Publish dates never change, so
a stale-but-present entry is still correct for versions it already knows; the
TTL exists so that newly published versions become visible.
"""

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_TTL = 24 * 3600
_SCHEMA = "v1"


def default_cache_dir() -> Path:
    env = os.environ.get("DEP_AGE_GATE_CACHE")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(base) / "dep-age-gate" / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "dep-age-gate"


class Cache:
    def __init__(self, directory=None, ttl: int = DEFAULT_TTL, enabled: bool = True):
        self.dir = Path(directory) if directory else default_cache_dir()
        self.ttl = ttl
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.dir / _SCHEMA / namespace / f"{digest}.json"

    def get(self, namespace: str, key: str):
        if not self.enabled:
            return None
        path = self._path(namespace, key)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                blob = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(blob, dict) or blob.get("key") != key:
            return None
        if time.time() - blob.get("fetched_at", 0) > self.ttl:
            return None
        self.hits += 1
        return blob.get("data")

    def put(self, namespace: str, key: str, data) -> None:
        if not self.enabled:
            return
        path = self._path(namespace, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            blob = {"key": key, "fetched_at": time.time(), "data": data}
            # Atomic replace so parallel threads never read a half-written file.
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(blob, handle)
            os.replace(tmp, path)
        except OSError:
            pass  # a cache that cannot be written must not break the check
