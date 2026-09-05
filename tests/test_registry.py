"""RegistryClient against a fake transport. No test here touches the network."""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from dep_age_gate.cache import Cache
from dep_age_gate.model import CRATES, MAVEN, NPM, PYPI, Dep
from dep_age_gate.registry import NotFound, RegistryClient, TransportError, normalize_pypi

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
MIN_AGE = 259200  # 72h


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


class FakeTransport:
    """Canned registry payloads, keyed by a substring of the request URL."""

    def __init__(self, json_payloads=None, last_modified=None):
        self.json_payloads = dict(json_payloads or {})
        self.last_modified = dict(last_modified or {})
        self.calls = []
        self._lock = threading.Lock()

    def _record(self, url):
        with self._lock:
            self.calls.append(url)

    def get_json(self, url, headers=None):
        self._record(url)
        for fragment, payload in self.json_payloads.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise NotFound(url)

    def head_last_modified(self, url, headers=None):
        self._record(url)
        for fragment, value in self.last_modified.items():
            if fragment in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise NotFound(url)


class ExplodingTransport:
    """Any network call is a test failure."""

    def get_json(self, url, headers=None):
        raise AssertionError(f"unexpected network call: {url}")

    def head_last_modified(self, url, headers=None):
        raise AssertionError(f"unexpected network call: {url}")


def default_transport():
    return FakeTransport(
        json_payloads={
            "registry.npmjs.org/left-pad": {
                "name": "left-pad",
                "time": {
                    "created": "2014-03-25T00:00:00.000Z",
                    "modified": "2018-05-01T00:00:00.000Z",
                    "1.2.0": "2017-01-01T00:00:00.000Z",
                    "1.3.0": "2018-05-01T09:15:30.000Z",
                },
            },
            "pypi.org/pypi/requests/2.32.3/json": {
                "urls": [
                    {"upload_time_iso_8601": "2024-05-29T15:37:49.685305Z", "packagetype": "bdist_wheel"},
                    {"upload_time_iso_8601": "2024-05-29T15:37:47.123456Z", "packagetype": "sdist"},
                ]
            },
            "crates.io/api/v1/crates/serde/versions": {
                "versions": [
                    {"num": "1.0.210", "created_at": "2024-09-09T18:23:41.123456789+00:00"},
                    {"num": "1.0.209", "created_at": "2024-08-22T10:00:00.000000+00:00"},
                ]
            },
            "search.maven.org/solrsearch": {
                "response": {
                    "numFound": 1,
                    "docs": [
                        {
                            "id": "com.google.guava:guava:33.0.0-jre",
                            "timestamp": 1704067200000,  # 2024-01-01T00:00:00Z
                        }
                    ],
                }
            },
        }
    )


# ---------------------------------------------------------------- published_at


def client(transport, cache=None, workers=1):
    return RegistryClient(
        cache=cache if cache is not None else Cache(enabled=False),
        transport=transport,
        workers=workers,
    )


def test_published_at_npm():
    got = client(default_transport()).published_at(Dep(NPM, "left-pad", "1.3.0"))
    assert got == datetime(2018, 5, 1, 9, 15, 30, tzinfo=timezone.utc)
    assert got.tzinfo is timezone.utc


def test_published_at_pypi_uses_the_earliest_file_upload():
    got = client(default_transport()).published_at(Dep(PYPI, "requests", "2.32.3"))
    assert got == datetime(2024, 5, 29, 15, 37, 47, 123456, tzinfo=timezone.utc)


def test_published_at_crates_trims_nanoseconds():
    got = client(default_transport()).published_at(Dep(CRATES, "serde", "1.0.210"))
    assert got == datetime(2024, 9, 9, 18, 23, 41, 123456, tzinfo=timezone.utc)


def test_published_at_maven_from_the_solrsearch_millisecond_timestamp():
    got = client(default_transport()).published_at(
        Dep(MAVEN, "com.google.guava:guava", "33.0.0-jre")
    )
    assert got == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_published_at_maven_falls_back_to_the_pom_last_modified_header():
    transport = FakeTransport(
        json_payloads={"search.maven.org": TransportError("HTTP 503")},
        last_modified={
            "repo1.maven.org/maven2/com/google/guava/guava/33.0.0-jre/":
                "Mon, 01 Jan 2024 00:00:00 GMT"
        },
    )
    got = client(transport).published_at(Dep(MAVEN, "com.google.guava:guava", "33.0.0-jre"))
    assert got == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_published_at_raises_not_found_for_an_unknown_npm_version():
    with pytest.raises(NotFound):
        client(default_transport()).published_at(Dep(NPM, "left-pad", "9.9.9"))


def test_published_at_raises_not_found_for_an_unknown_crate_version():
    with pytest.raises(NotFound):
        client(default_transport()).published_at(Dep(CRATES, "serde", "9.9.9"))


def test_published_at_rejects_an_unknown_ecosystem():
    with pytest.raises(TransportError):
        client(default_transport()).published_at(Dep("nuget", "Newtonsoft.Json", "13.0.3"))


def test_normalize_pypi():
    assert normalize_pypi("Flask_SQLAlchemy.Ext") == "flask-sqlalchemy-ext"


# ---------------------------------------------------------------- evaluate


def age_transport():
    """left-pad@1.3.0 published 10h ago, lodash@4.17.21 published 30d ago."""
    return FakeTransport(
        json_payloads={
            "registry.npmjs.org/left-pad": {
                "time": {"created": iso(NOW), "1.3.0": iso(NOW - timedelta(hours=10))}
            },
            "registry.npmjs.org/lodash": {
                "time": {"created": iso(NOW), "4.17.21": iso(NOW - timedelta(days=30))}
            },
        }
    )


def test_evaluate_fails_a_ten_hour_old_version_and_passes_a_thirty_day_old_one():
    deps = [Dep(NPM, "left-pad", "1.3.0"), Dep(NPM, "lodash", "4.17.21")]
    results = client(age_transport()).evaluate(deps, MIN_AGE, now=NOW)

    by_name = {r.dep.name: r for r in results}
    assert by_name["left-pad"].status == "FAIL"
    assert by_name["left-pad"].age_seconds == pytest.approx(10 * 3600)
    assert by_name["left-pad"].failed is True

    assert by_name["lodash"].status == "PASS"
    assert by_name["lodash"].age_seconds == pytest.approx(30 * 86400)
    assert by_name["lodash"].failed is False


def test_evaluate_marks_a_missing_version_unknown_not_pass():
    deps = [Dep(NPM, "left-pad", "1.9.9")]
    results = client(age_transport()).evaluate(deps, MIN_AGE, now=NOW)
    assert results[0].status == "UNKNOWN"
    assert results[0].published is None
    assert results[0].failed is True
    assert "version not in registry" in results[0].message


def test_evaluate_marks_a_transport_failure_unknown_not_pass():
    transport = FakeTransport(
        json_payloads={"registry.npmjs.org/left-pad": TransportError("HTTP 500")}
    )
    results = client(transport).evaluate([Dep(NPM, "left-pad", "1.3.0")], MIN_AGE, now=NOW)
    assert results[0].status == "UNKNOWN"
    assert results[0].failed is True


def test_evaluate_allow_mapping_yields_allowed_with_the_reason():
    deps = [Dep(NPM, "left-pad", "1.3.0"), Dep(NPM, "lodash", "4.17.21")]
    allow = {(NPM, "left-pad", "1.3.0"): "CVE-2026-1 hotfix, approved by security"}
    results = client(age_transport()).evaluate(deps, MIN_AGE, now=NOW, allow=allow)

    by_name = {r.dep.name: r for r in results}
    assert by_name["left-pad"].status == "ALLOWED"
    assert by_name["left-pad"].allow_reason == "CVE-2026-1 hotfix, approved by security"
    assert by_name["left-pad"].failed is False
    assert by_name["lodash"].status == "PASS"


def test_evaluate_allow_wildcard_version():
    deps = [Dep(NPM, "left-pad", "1.3.0")]
    allow = {(NPM, "left-pad", "*"): "vendored, we build it ourselves"}
    results = client(age_transport()).evaluate(deps, MIN_AGE, now=NOW, allow=allow)
    assert results[0].status == "ALLOWED"
    assert results[0].allow_reason == "vendored, we build it ourselves"


def test_evaluate_never_calls_the_registry_for_an_allowed_dep():
    transport = ExplodingTransport()
    allow = {(NPM, "left-pad", "1.3.0"): "approved"}
    results = client(transport).evaluate([Dep(NPM, "left-pad", "1.3.0")], MIN_AGE, now=NOW, allow=allow)
    assert results[0].status == "ALLOWED"


def test_evaluate_looks_a_duplicated_version_up_once_but_reports_it_twice():
    transport = age_transport()
    deps = [
        Dep(NPM, "lodash", "4.17.21", source="a/package-lock.json"),
        Dep(NPM, "lodash", "4.17.21", source="b/package-lock.json"),
    ]
    results = client(transport).evaluate(deps, MIN_AGE, now=NOW)
    assert len(results) == 2
    assert [r.dep.source for r in results] == ["a/package-lock.json", "b/package-lock.json"]
    assert len(transport.calls) == 1


# ---------------------------------------------------------------- cache


def test_two_lookups_hit_the_npm_registry_once(tmp_path):
    transport = age_transport()
    registry = client(transport, cache=Cache(directory=tmp_path))
    first = registry.published_at(Dep(NPM, "lodash", "4.17.21"))
    second = registry.published_at(Dep(NPM, "lodash", "4.17.21"))
    assert first == second
    assert len(transport.calls) == 1


def test_the_cache_persists_across_a_second_registry_client(tmp_path):
    warm = client(age_transport(), cache=Cache(directory=tmp_path))
    expected = warm.published_at(Dep(NPM, "lodash", "4.17.21"))

    cold = client(ExplodingTransport(), cache=Cache(directory=tmp_path))
    assert cold.published_at(Dep(NPM, "lodash", "4.17.21")) == expected
    assert cold.cache.hits == 1


def test_a_disabled_cache_writes_nothing(tmp_path):
    transport = age_transport()
    registry = client(transport, cache=Cache(directory=tmp_path, enabled=False))
    registry.published_at(Dep(NPM, "lodash", "4.17.21"))
    registry.published_at(Dep(NPM, "lodash", "4.17.21"))
    assert len(transport.calls) == 2
    assert list(tmp_path.iterdir()) == []


def test_an_expired_cache_entry_is_refetched(tmp_path):
    transport = age_transport()
    registry = client(transport, cache=Cache(directory=tmp_path, ttl=0))
    registry.published_at(Dep(NPM, "lodash", "4.17.21"))
    registry.published_at(Dep(NPM, "lodash", "4.17.21"))
    assert len(transport.calls) == 2


def test_pypi_cache_key_is_the_normalised_name(tmp_path):
    transport = FakeTransport(
        json_payloads={
            "pypi.org/pypi/Flask_SQLAlchemy/3.1.1/json": {
                "urls": [{"upload_time_iso_8601": "2023-09-11T20:00:00.000000Z"}]
            },
            "pypi.org/pypi/flask-sqlalchemy/3.1.1/json": {
                "urls": [{"upload_time_iso_8601": "2023-09-11T20:00:00.000000Z"}]
            },
        }
    )
    cache = Cache(directory=tmp_path)
    registry = client(transport, cache=cache)
    registry.published_at(Dep(PYPI, "Flask_SQLAlchemy", "3.1.1"))
    registry.published_at(Dep(PYPI, "flask-sqlalchemy", "3.1.1"))
    assert len(transport.calls) == 1
    assert cache.hits == 1
