"""Selected-source checks, with fake credentials and local HTTP fixtures only."""

import json
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from dep_age_gate import audit, cli, python_index
from dep_age_gate.cache import Cache
from dep_age_gate.model import PYPI, Dep
from dep_age_gate.parsers import parse
from dep_age_gate.python_index import (
    CONFIG_ENV, PythonIndexError, SimpleIndexTransport, canonical_python_index,
    configured_python_index, simple_published_at,
)
from dep_age_gate.registry import RegistryClient
from test_registry import ExplodingTransport
from test_registry_sources import locktext

INDEX = "https://packages.example.com/simple"
SECOND = "https://other.example.com/python/simple"
OLD = "2020-01-01T00:00:00Z"
NEW = "2026-09-18T00:00:00Z"
NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def payload(stamp=OLD):
    return {
        "meta": {"api-version": "1.1"}, "name": "requests",
        "files": [{"filename": "requests-2.32.3-py3-none-any.whl", "upload-time": stamp,
                   "url": "http://169.254.169.254/never-fetch-artifacts"}],
    }


class MetadataTransport:
    def __init__(self, data=None):
        self.data = data if data is not None else payload()
        self.calls = []

    def get_json(self, url, headers):
        self.calls.append((url, headers))
        return self.data


@pytest.fixture(autouse=True)
def trusted_indexes(monkeypatch):
    monkeypatch.setenv(CONFIG_ENV, json.dumps([{"url": INDEX}, {"url": SECOND}]))


def client(transport):
    return RegistryClient(cache=Cache(enabled=False), transport=ExplodingTransport(),
                          index_transport=transport, workers=1)


@pytest.mark.parametrize("path", ["uv.lock", "poetry.lock"])
def test_configured_registry_preserves_source_and_queries_it(path):
    outcome = parse(path, locktext(path, INDEX), path)
    assert outcome.errors == outcome.skips == []
    assert outcome.deps[0].key == (PYPI, "requests", "2.32.3", INDEX)
    metadata = MetadataTransport()
    result = client(metadata).evaluate(outcome.deps, 259200, now=NOW)[0]
    assert result.status == "PASS"
    assert metadata.calls == [(INDEX + "/requests/", {"Accept": "application/vnd.pypi.simple.v1+json"})]


@pytest.mark.parametrize("path", ["uv.lock", "poetry.lock"])
@pytest.mark.parametrize("before,after,count", [
    ("https://pypi.org/simple", INDEX, 1), (INDEX, "https://pypi.org/simple", 1),
    (INDEX, SECOND, 1), (INDEX, INDEX, 0), (INDEX, INDEX + "/", 0),
])
def test_same_version_selected_registry_changes_are_checked(path, before, after, count):
    deps, skips, errors = audit.collect([audit.Target(path, locktext(path, after), locktext(path, before))])
    assert skips == errors == []
    assert len(deps) == count


def test_dedup_and_public_exceptions_do_not_hide_a_different_registry():
    deps = [Dep(PYPI, "requests", "2.32.3", registry=r) for r in (INDEX, SECOND)]
    metadata = MetadataTransport(payload(NEW))
    allow = {(PYPI, "requests", "2.32.3"): "public exception", (PYPI, "requests", "*"): "public wildcard"}
    results = client(metadata).evaluate(deps, 259200, now=NOW, allow=allow)
    assert [r.status for r in results] == ["FAIL", "FAIL"]
    assert [url for url, _ in metadata.calls] == [INDEX + "/requests/", SECOND + "/requests/"]


def test_custom_metadata_cannot_reuse_public_cache_or_persist_private_data(tmp_path):
    cache = Cache(directory=tmp_path)
    cache.put(PYPI, "requests@2.32.3", OLD)
    original = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    metadata = MetadataTransport(payload(NEW))
    registry = RegistryClient(cache=cache, transport=ExplodingTransport(), index_transport=metadata)
    result = registry.evaluate([Dep(PYPI, "requests", "2.32.3", registry=INDEX)], 259200, now=NOW)[0]
    assert result.status == "FAIL"
    assert len(metadata.calls) == 1
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == original


def test_unconfigured_registry_is_never_contacted(monkeypatch):
    monkeypatch.delenv(CONFIG_ENV)
    metadata = MetadataTransport()
    results = client(metadata).evaluate([Dep(PYPI, "requests", "2.32.3", registry=INDEX)], 259200)
    assert results[0].status == "UNKNOWN"
    assert CONFIG_ENV in results[0].message
    assert metadata.calls == []


@pytest.mark.parametrize("raw", [
    "not json", "{}", '[null]', '[{"url":42}]',
    '[{"url":"https://user:secret@example.com/simple"}]',
    '[{"url":"https://packages.example.com/simple","token":"secret"}]',
    '[{"url":"https://packages.example.com/simple","username_env":"USER"}]',
    '[{"url":"https://packages.example.com/simple","username_env":5,"password_env":"PASS"}]',
    json.dumps([{"url": INDEX}, {"url": INDEX + "/"}]),
])
def test_malformed_configuration_fails_closed_without_echoing_it(monkeypatch, raw):
    monkeypatch.setenv(CONFIG_ENV, raw)
    with pytest.raises(PythonIndexError, match="invalid DEP_AGE_GATE_PYTHON_INDEXES") as error:
        configured_python_index(INDEX)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("url", [
    "http://packages.example.com/simple", "https://user:secret@packages.example.com/simple",
    "https://packages.example.com/simple?token=secret", "https://packages.example.com/simple#secret",
    "https://packages.example.com/simple/../admin", "https://packages.example.com/%2e%2e/admin",
    "https://packages.example.com\\@evil.com/simple", "https://packages.example.com/simple\n",
    "https://packages.example.com:0/simple", "https://packages.example.com:invalid/simple",
    "https://packages.example.com//simple", "https://packages.example.com./simple",
])
def test_unsafe_registry_identity_is_rejected(url):
    with pytest.raises(PythonIndexError, match="unsupported registry") as error:
        canonical_python_index(url)
    assert "secret" not in str(error.value)


def test_index_authorization_is_exact_not_a_host_prefix(monkeypatch):
    assert configured_python_index("https://PACKAGES.example.com:443/simple/") == INDEX
    for different in (INDEX + "/other", "https://packages.example.com.evil.com/simple", "https://packages.example.com:444/simple"):
        with pytest.raises(PythonIndexError, match="configure its exact URL"):
            configured_python_index(different)


def test_devpi_style_simple_path_retains_its_identity():
    url = "https://packages.example.com/user/index/+simple"
    assert canonical_python_index(url + "/") == url


def test_environment_credentials_are_bound_to_one_exact_index(monkeypatch):
    monkeypatch.setenv(CONFIG_ENV, json.dumps([
        {"url": INDEX, "username_env": "UV_INDEX_INTERNAL_USERNAME", "password_env": "UV_INDEX_INTERNAL_PASSWORD"},
        {"url": SECOND},
    ]))
    monkeypatch.setenv("UV_INDEX_INTERNAL_USERNAME", "fixture-user")
    monkeypatch.setenv("UV_INDEX_INTERNAL_PASSWORD", "fixture-password")
    metadata = MetadataTransport()
    for registry in (INDEX, SECOND):
        simple_published_at(registry, "requests", "2.32.3", metadata)
    assert metadata.calls[0][1]["Authorization"] == "Basic Zml4dHVyZS11c2VyOmZpeHR1cmUtcGFzc3dvcmQ="
    assert "Authorization" not in metadata.calls[1][1]
    monkeypatch.delenv("UV_INDEX_INTERNAL_PASSWORD")
    with pytest.raises(PythonIndexError, match="credentials are missing"):
        simple_published_at(INDEX, "requests", "2.32.3", metadata)
    assert len(metadata.calls) == 2


@pytest.mark.parametrize("stamp", [None, 42, "yesterday", "2020-01-01", "2020-01-01T00:00:00",
    "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00.1234567Z", "2020-02-31T00:00:00Z"])
def test_missing_or_invalid_timestamps_are_unknown(stamp):
    result = client(MetadataTransport(payload(stamp))).evaluate(
        [Dep(PYPI, "requests", "2.32.3", registry=INDEX)], 259200, now=NOW)[0]
    assert result.status == "UNKNOWN"
    assert "upload-time" in result.message


def test_new_wheel_on_old_version_does_not_inherit_old_sdist_date():
    data = payload(NEW)
    data["files"].append({"filename": "requests-2.32.3.tar.gz", "upload-time": OLD})
    result = client(MetadataTransport(data)).evaluate(
        [Dep(PYPI, "requests", "2.32.3", registry=INDEX)], 259200, now=NOW)[0]
    assert result.status == "FAIL"
    assert result.published == datetime(2026, 9, 18, tzinfo=timezone.utc)


def test_one_missing_file_timestamp_fails_the_entire_version():
    data = payload()
    data["files"].append({"filename": "requests-2.32.3.tar.gz"})
    with pytest.raises(PythonIndexError, match="missing or invalid"):
        simple_published_at(INDEX, "requests", "2.32.3", MetadataTransport(data))


@pytest.mark.parametrize("filename", ["other-2.32.3.tar.gz", "requests-2.32.30.tar.gz",
    "requests-2.32.3rc1-py3-none-any.whl", "requests-2.32.3.exe", "../../requests-2.32.3.tar.gz"])
def test_other_names_versions_and_unknown_artifacts_cannot_supply_timestamps(filename):
    data = payload()
    data["files"][0]["filename"] = filename
    with pytest.raises(PythonIndexError, match="no verifiable files"):
        simple_published_at(INDEX, "requests", "2.32.3", MetadataTransport(data))


@pytest.mark.parametrize("data", [None, [], {"meta": {}},
    {"meta": {"api-version": "1.0"}, "name": "requests", "files": []},
    {"meta": {"api-version": "2.0"}, "name": "requests", "files": []},
    {"meta": {"api-version": "1.1"}, "name": "other", "files": []},
    {"meta": {"api-version": "1.1"}, "name": "requests", "files": [None]},
])
def test_invalid_metadata_never_passes(data):
    metadata = MetadataTransport()
    metadata.data = data
    result = client(metadata).evaluate([Dep(PYPI, "requests", "2.32.3", registry=INDEX)], 259200)[0]
    assert result.status == "UNKNOWN"


def test_cli_reports_missing_timestamp_failure_without_source_or_credentials(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "uv.lock"
    lock.write_text(locktext("uv.lock", INDEX), encoding="utf-8")
    metadata = MetadataTransport(payload(None))
    monkeypatch.setattr(cli, "RegistryClient", lambda **kwargs: client(metadata))
    assert cli.main(["audit", "--lock", str(lock), "--all", "--json"]) == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["results"][0]["status"] == "UNKNOWN"
    assert len(report["results"][0]["registry_id"]) == 16
    assert "upload-time" in report["results"][0]["message"]
    assert "packages.example.com" not in output.out + output.err


@pytest.fixture
def local_registry(monkeypatch):
    """Exercise real HTTP framing locally; TLS is a fake, not TLS acceptance."""
    state = SimpleNamespace(status=200, content_type="application/vnd.pypi.simple.v1+json",
                            body=json.dumps(payload()).encode(), calls=[], declared_length=None, delay=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state.calls.append((self.path, self.headers.get("Authorization")))
            if state.delay:
                time.sleep(state.delay)
                return
            self.send_response(state.status)
            self.send_header("Content-Type", state.content_type)
            self.send_header("Location", "http://169.254.169.254/secret-target")
            self.send_header("Last-Modified", "Wed, 01 Jan 2020 00:00:00 GMT")
            self.send_header("Content-Length", str(state.declared_length if state.declared_length is not None else len(state.body)))
            self.end_headers()
            try:
                self.wfile.write(state.body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_resolve = socket.getaddrinfo
    monkeypatch.setattr(python_index.socket, "getaddrinfo", lambda *args, **kwargs: real_resolve("127.0.0.1", server.server_port, type=socket.SOCK_STREAM))
    monkeypatch.setattr(python_index, "_public_address", lambda value: True)

    class FakeTLS:
        def wrap_socket(self, sock, server_hostname):
            assert server_hostname == "packages.example.com"
            return sock

    monkeypatch.setattr(python_index.ssl, "create_default_context", FakeTLS)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_http_request_path_and_authorization(local_registry):
    data = SimpleIndexTransport().get_json(INDEX + "/requests/", {"Authorization": "Basic fixture-only"})
    assert data == payload()
    assert local_registry.calls == [("/simple/requests/", "Basic fixture-only")]


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 404, 429, 500])
def test_http_errors_and_redirects_are_not_retried_or_followed(local_registry, status):
    local_registry.status = status
    with pytest.raises(PythonIndexError, match=f"HTTP {status}") as error:
        SimpleIndexTransport().get_json(INDEX + "/requests/", {"Authorization": "Basic fixture-only"})
    assert len(local_registry.calls) == 1
    assert "secret" not in str(error.value)


def test_html_and_last_modified_never_become_a_publication_time(local_registry):
    local_registry.content_type = "text/html"
    with pytest.raises(PythonIndexError, match="does not serve Simple JSON"):
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})


def test_oversized_response_is_rejected_before_read(local_registry):
    local_registry.declared_length = python_index.MAX_RESPONSE_BYTES + 1
    with pytest.raises(PythonIndexError, match="response limit"):
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})


def test_invalid_json_does_not_echo_server_body(local_registry):
    local_registry.body = b"secret credentials server traceback"
    with pytest.raises(PythonIndexError, match="JSON decoding failed") as error:
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})
    assert "secret" not in str(error.value)


def test_actual_body_limit_is_enforced(local_registry, monkeypatch):
    # No oversized Content-Length hint: enforce the count of bytes read too.
    monkeypatch.setattr(python_index, "MAX_RESPONSE_BYTES", 32)
    local_registry.declared_length = ""
    # A no-length response is valid HTTP/1.0. Remove the header in a transport
    # boundary fixture so this exercises streaming size enforcement.
    original = python_index.http.client.HTTPResponse.getheader
    monkeypatch.setattr(python_index.http.client.HTTPResponse, "getheader",
                        lambda self, name, default=None: None if name == "Content-Length" else original(self, name, default))
    with pytest.raises(PythonIndexError, match="response limit"):
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})


def test_truncated_response_is_rejected(local_registry):
    local_registry.declared_length = len(local_registry.body) + 10
    with pytest.raises(PythonIndexError, match="truncated"):
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})


def test_stalled_headers_are_bounded_by_request_deadline(local_registry, monkeypatch):
    monkeypatch.setattr(python_index, "REQUEST_TIMEOUT", 0.2)
    local_registry.delay = 2
    start = time.monotonic()
    with pytest.raises(PythonIndexError, match="request or JSON decoding failed"):
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})
    assert time.monotonic() - start < 1.5


def test_mixed_public_private_dns_answer_is_rejected(monkeypatch):
    monkeypatch.setattr(python_index.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
        for ip in ("8.8.8.8", "127.0.0.1")
    ])
    with pytest.raises(PythonIndexError, match="non-public address"):
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "fc00::1", "224.0.0.1",
    "192.0.0.8", "64:ff9b::7f00:1", "2002:7f00:0001::", "::ffff:127.0.0.1"])
def test_nonpublic_dns_is_rejected_before_connect(monkeypatch, ip):
    monkeypatch.setattr(python_index.socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])
    with pytest.raises(PythonIndexError, match="non-public address"):
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})


def test_dns_error_is_sanitized(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("secret-url-with-credentials")
    monkeypatch.setattr(python_index.socket, "getaddrinfo", fail)
    with pytest.raises(PythonIndexError, match="request or JSON decoding failed") as error:
        SimpleIndexTransport().get_json(INDEX + "/requests/", {})
    assert "secret" not in str(error.value)
