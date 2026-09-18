"""Publish-date lookups.

npm     GET https://registry.npmjs.org/<pkg>                 -> time[<version>]
PyPI    GET https://pypi.org/pypi/<pkg>/<ver>/json           -> urls[].upload_time_iso_8601
crates  GET https://crates.io/api/v1/crates/<crate>/versions -> versions[].created_at
maven   GET https://search.maven.org/solrsearch/select?...   -> response.docs[0].timestamp (ms)
        fallback: HEAD https://repo1.maven.org/... .pom      -> Last-Modified

Every lookup goes through Cache. Fetches run on a thread pool because each one
is a network round trip and lockfiles routinely hold hundreds of versions.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from dep_age_gate import __version__
from dep_age_gate.cache import Cache
from dep_age_gate.model import CRATES, MAVEN, NPM, PYPI, Dep, Result
from dep_age_gate.python_index import PythonIndexError, SimpleIndexTransport, simple_published_at

USER_AGENT = f"dep-age-gate/{__version__} (+https://github.com/th3nolo/dep-age-gate)"
TIMEOUT = 20
RETRIES = 3


class NotFound(Exception):
    pass


class TransportError(Exception):
    pass


class HttpTransport:
    """Real network access. Tests inject a fake with the same two methods."""

    def __init__(self, timeout: int = TIMEOUT, retries: int = RETRIES):
        self.timeout = timeout
        self.retries = retries

    def get_json(self, url: str, headers=None):
        raw = self._get(url, headers)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise TransportError(f"{url}: response was not JSON ({exc})") from exc

    def head_last_modified(self, url: str, headers=None):
        request = urllib.request.Request(url, method="HEAD")
        request.add_header("User-Agent", USER_AGENT)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.headers.get("Last-Modified")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound(url) from exc
            raise TransportError(f"{url}: HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001 - urllib raises many types
            raise TransportError(f"{url}: {exc}") from exc

    def _get(self, url: str, headers=None) -> bytes:
        last = None
        for attempt in range(self.retries):
            request = urllib.request.Request(url)
            request.add_header("User-Agent", USER_AGENT)
            for key, value in (headers or {}).items():
                request.add_header(key, value)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise NotFound(url) from exc
                last = TransportError(f"{url}: HTTP {exc.code}")
                if exc.code < 500 and exc.code != 429:
                    break
            except Exception as exc:  # noqa: BLE001
                last = TransportError(f"{url}: {exc}")
        raise last or TransportError(f"{url}: unknown transport failure")


# ---------------------------------------------------------------- helpers


def _iso(text: str):
    if not text:
        return None
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    # PyPI sends 6-digit microseconds; crates.io sends 6 or 9. Trim to 6.
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_pypi(name: str) -> str:
    """PEP 503 normalisation: lowercase, runs of - _ . collapse to a dash."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------- client


class RegistryClient:
    def __init__(self, cache: Cache = None, transport=None, workers: int = 12, index_transport=None):
        self.cache = cache if cache is not None else Cache()
        self.transport = transport or HttpTransport()
        self.workers = max(1, workers)
        self.index_transport = index_transport or SimpleIndexTransport()

    # -- per-ecosystem lookups -------------------------------------------

    def _npm_times(self, package: str) -> dict:
        cached = self.cache.get(NPM, package)
        if cached is not None:
            return cached
        url = "https://registry.npmjs.org/" + urllib.parse.quote(package, safe="@")
        data = self.transport.get_json(url)
        times = {
            key: value
            for key, value in (data.get("time") or {}).items()
            if key not in ("created", "modified")
        }
        self.cache.put(NPM, package, times)
        return times

    def _pypi_time(self, package: str, version: str):
        key = f"{normalize_pypi(package)}@{version}"
        cached = self.cache.get(PYPI, key)
        if cached is not None:
            return cached
        url = "https://pypi.org/pypi/{}/{}/json".format(
            urllib.parse.quote(package, safe=""), urllib.parse.quote(version, safe="")
        )
        data = self.transport.get_json(url)
        stamps = [
            item.get("upload_time_iso_8601")
            for item in (data.get("urls") or [])
            if item.get("upload_time_iso_8601")
        ]
        if not stamps:
            # A version with no files left (yanked/deleted sdist) still has a
            # release entry on the project endpoint.
            raise NotFound(f"pypi:{package}@{version}: no files with an upload time")
        value = min(stamps)
        self.cache.put(PYPI, key, value)
        return value

    def _crates_times(self, crate: str) -> dict:
        cached = self.cache.get(CRATES, crate)
        if cached is not None:
            return cached
        url = "https://crates.io/api/v1/crates/{}/versions".format(
            urllib.parse.quote(crate, safe="")
        )
        # crates.io rejects requests without a descriptive User-Agent.
        data = self.transport.get_json(url, headers={"User-Agent": USER_AGENT})
        times = {
            item["num"]: item["created_at"]
            for item in (data.get("versions") or [])
            if item.get("num") and item.get("created_at")
        }
        self.cache.put(CRATES, crate, times)
        return times

    def _maven_time(self, coordinate: str, version: str):
        key = f"{coordinate}:{version}"
        cached = self.cache.get(MAVEN, key)
        if cached is not None:
            return cached
        group, artifact = coordinate.split(":", 1)
        query = "g:{} AND a:{} AND v:{}".format(group, artifact, version)
        url = "https://search.maven.org/solrsearch/select?" + urllib.parse.urlencode(
            {"q": query, "rows": 1, "wt": "json", "core": "gav"}
        )
        value = None
        try:
            data = self.transport.get_json(url)
            docs = ((data.get("response") or {}).get("docs")) or []
            if docs and docs[0].get("timestamp"):
                stamp = datetime.fromtimestamp(
                    docs[0]["timestamp"] / 1000.0, tz=timezone.utc
                )
                value = stamp.isoformat().replace("+00:00", "Z")
        except (TransportError, NotFound):
            value = None
        if value is None:
            value = self._maven_last_modified(group, artifact, version)
        if value is None:
            raise NotFound(f"maven:{coordinate}:{version}")
        self.cache.put(MAVEN, key, value)
        return value

    def _maven_last_modified(self, group: str, artifact: str, version: str):
        path = "{}/{}/{}/{}-{}.pom".format(
            group.replace(".", "/"), artifact, version, artifact, version
        )
        url = "https://repo1.maven.org/maven2/" + path
        try:
            header = self.transport.head_last_modified(url)
        except (TransportError, NotFound):
            return None
        if not header:
            return None
        try:
            return (
                parsedate_to_datetime(header)
                .astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (TypeError, ValueError):
            return None

    # -- public API -------------------------------------------------------

    def published_at(self, dep: Dep):
        """Return a timezone-aware UTC datetime. Raise NotFound / TransportError."""
        if dep.ecosystem == NPM:
            times = self._npm_times(dep.name)
            raw = times.get(dep.version)
            if raw is None:
                raise NotFound(f"npm:{dep.name}@{dep.version}: version not in registry")
        elif dep.ecosystem == PYPI:
            if dep.registry:
                try:
                    return simple_published_at(dep.registry, dep.name, dep.version, self.index_transport)
                except PythonIndexError as exc:
                    raise TransportError(str(exc)) from None
            raw = self._pypi_time(dep.name, dep.version)
        elif dep.ecosystem == CRATES:
            times = self._crates_times(dep.name)
            raw = times.get(dep.version)
            if raw is None:
                raise NotFound(
                    f"crates:{dep.name}@{dep.version}: version not in registry"
                )
        elif dep.ecosystem == MAVEN:
            raw = self._maven_time(dep.name, dep.version)
        else:
            raise TransportError(f"unknown ecosystem {dep.ecosystem!r}")

        parsed = _iso(raw)
        if parsed is None:
            raise TransportError(f"{dep.label()}: unreadable publish date {raw!r}")
        return parsed

    def evaluate(self, deps, min_age_seconds: int, now=None, allow=None):
        """Look every dep up in parallel and grade it. Returns list[Result]."""
        now = now or datetime.now(timezone.utc)
        allow = allow or {}
        unique = {}
        for dep in deps:
            unique.setdefault(dep.key, dep)

        def work(dep: Dep) -> Result:
            # Existing public-registry exceptions must not authorize a different source.
            if dep.key in allow or (not dep.registry and (dep.ecosystem, dep.name, "*") in allow):
                reason = allow.get(dep.key) or allow.get((dep.ecosystem, dep.name, "*"))
                return Result(dep=dep, status="ALLOWED", allow_reason=reason)
            try:
                published = self.published_at(dep)
            except NotFound as exc:
                return Result(dep=dep, status="UNKNOWN", message=str(exc))
            except TransportError as exc:
                return Result(dep=dep, status="UNKNOWN", message=str(exc))
            age = (now - published).total_seconds()
            status = "PASS" if age >= min_age_seconds else "FAIL"
            return Result(dep=dep, published=published, age_seconds=age, status=status)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            graded = list(pool.map(work, unique.values()))

        by_key = {result.dep.key: result for result in graded}
        out = []
        for dep in deps:
            base = by_key[dep.key]
            out.append(
                Result(
                    dep=dep,
                    published=base.published,
                    age_seconds=base.age_seconds,
                    status=base.status,
                    message=base.message,
                    allow_reason=base.allow_reason,
                )
            )
        return out
