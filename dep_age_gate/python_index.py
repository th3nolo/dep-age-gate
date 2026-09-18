"""Explicitly trusted Python Simple JSON indexes (PEP 691 / PEP 700).

Lockfiles are untrusted input. Only runner-owned environment configuration can
authorize an endpoint or select credential environment variables. No redirects,
ambient proxy credentials, artifact downloads, or public-PyPI fallback are used.
"""

import base64
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

CONFIG_ENV = "DEP_AGE_GATE_PYTHON_INDEXES"
PUBLIC_INDEX = "https://pypi.org/simple"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT = 20
MAX_ADDRESSES = 8
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast:
        return False
    # Be conservative across supported Python versions' differing special-use
    # classifications. Transition/translation ranges can hide private IPv4.
    blocked = ("192.0.0.0/24",) if address.version == 4 else (
        "64:ff9b::/96", "64:ff9b:1::/48", "2001::/23", "2002::/16", "::ffff:0:0/96",
    )
    return not any(address in ipaddress.ip_network(network) for network in blocked)


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class PythonIndexError(ValueError):
    """A safe diagnostic, never containing URLs, credentials or response text."""


def canonical_python_index(url: str) -> str:
    """Return a canonical source identity; empty means the public PyPI index."""
    if not isinstance(url, str) or not url or re.search(r"[\s\\%?#]", url):
        raise PythonIndexError("unsupported registry: expected a credential-free HTTPS index")
    try:
        parts = urlsplit(url)
        port = parts.port
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment
                or not re.fullmatch(r"[A-Za-z0-9.-]+", parts.hostname)
                or not re.fullmatch(r"/[A-Za-z0-9/_.~-]*", parts.path)
                or any(p in (".", "..") for p in parts.path.split("/"))
                or "//" in parts.path or port == 0):
            raise ValueError
        host = parts.hostname.lower()
        if host.endswith(".") or not all(host.split(".")):
            raise ValueError
        authority = host if port in (None, 443) else f"{host}:{port}"
        canonical = f"https://{authority}{parts.path.rstrip('/')}"
    except ValueError:
        raise PythonIndexError("unsupported registry: expected a credential-free HTTPS index") from None
    return "" if canonical == PUBLIC_INDEX else canonical


def _configuration(registry: str) -> dict[str, str]:
    try:
        entries = json.loads(os.environ.get(CONFIG_ENV, "[]"))
        if not isinstance(entries, list):
            raise ValueError
        matched = None
        seen = set()
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) - {"url", "username_env", "password_env"}
                    or not isinstance(entry.get("url"), str)):
                raise ValueError
            identity = canonical_python_index(entry["url"])
            if not identity or identity in seen:
                raise ValueError
            seen.add(identity)
            if ("username_env" in entry) != ("password_env" in entry):
                raise ValueError
            for key in ("username_env", "password_env"):
                if key in entry and (not isinstance(entry[key], str) or not _ENV_NAME.fullmatch(entry[key])):
                    raise ValueError
            if identity == registry:
                matched = entry
    except (ValueError, TypeError, RecursionError):
        raise PythonIndexError("invalid " + CONFIG_ENV + " configuration") from None
    if matched is None:
        raise PythonIndexError("unsupported registry: configure its exact URL in " + CONFIG_ENV)
    return matched


def configured_python_index(url: str) -> str:
    """Validate syntax and authorization before including a lockfile entry."""
    identity = canonical_python_index(url)
    if identity:
        _configuration(identity)
    return identity


def _headers(registry: str) -> dict[str, str]:
    config = _configuration(registry)
    headers = {"Accept": "application/vnd.pypi.simple.v1+json"}
    if "username_env" in config:
        username = os.environ.get(config["username_env"])
        password = os.environ.get(config["password_env"])
        if not username or not password or ":" in username or any(c in username + password for c in "\r\n"):
            raise PythonIndexError("Python index credentials are missing or invalid")
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        headers["Authorization"] = "Basic " + credentials
    return headers


class SimpleIndexTransport:
    """Bounded HTTPS to a validated public IP, retaining TLS hostname checks.

    DNS is resolved once; the connection uses that exact address, preventing a
    second resolution from bypassing the private-address check. Private-network
    indexes are deliberately unsupported. Requests are never retried/redirected.
    """

    def get_json(self, url: str, headers: dict[str, str]) -> object:
        parts = urlsplit(url)
        connection = None
        response = None
        raw_socket = None
        deadline = None
        started = time.monotonic()
        try:
            addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
            if not addresses or len(addresses) > MAX_ADDRESSES:
                raise PythonIndexError("Python index DNS address count is unsupported")
            for address in addresses:
                if not _public_address(address[4][0]):
                    raise PythonIndexError("Python index resolves to a non-public address")
            family, socktype, proto, _, sockaddr = addresses[0]
            raw_socket = socket.socket(family, socktype, proto)
            raw_socket.settimeout(REQUEST_TIMEOUT)
            raw_socket.connect(sockaddr)
            secured = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parts.hostname)
            remaining = REQUEST_TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                secured.close()
                raise PythonIndexError("Python index request deadline exceeded")
            deadline = threading.Timer(remaining, _shutdown, args=(secured,))
            deadline.daemon = True
            deadline.start()
            connection = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=REQUEST_TIMEOUT)
            connection.sock = secured
            connection.request("GET", parts.path, headers=headers)
            # Read directly so HTTP/1.0's Connection: close does not close the
            # socket before we can enforce the response-body timeout.
            response = http.client.HTTPResponse(secured, method="GET")
            response.begin()
            if response.status != 200:
                raise PythonIndexError(f"Python index HTTP {response.status}; redirects are not followed")
            if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/vnd.pypi.simple.v1+json":
                raise PythonIndexError("Python index does not serve Simple JSON metadata")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                raise PythonIndexError("Python index metadata exceeds response limit")
            chunks = []
            size = 0
            while True:
                remaining = REQUEST_TIMEOUT - (time.monotonic() - started)
                if remaining <= 0:
                    raise PythonIndexError("Python index request deadline exceeded")
                secured.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise PythonIndexError("Python index metadata exceeds response limit")
                chunks.append(chunk)
            if length is not None and size != int(length):
                raise PythonIndexError("Python index metadata response was truncated")
            return json.loads(b"".join(chunks))
        except PythonIndexError:
            raise
        except (OSError, ValueError, RecursionError, http.client.HTTPException):
            raise PythonIndexError("Python index request or JSON decoding failed") from None
        finally:
            if deadline is not None:
                deadline.cancel()
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
            if raw_socket is not None:
                raw_socket.close()


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _file_matches(filename: str, name: str, version: str) -> bool:
    """Match standard wheels/sdists exactly; unfamiliar names fail closed."""
    if filename.endswith(".whl"):
        fields = filename[:-4].split("-")
        return (len(fields) in (5, 6) and _normalize(fields[0]) == name
                and fields[1] == version)
    for extension in (".tar.gz", ".zip"):
        if filename.endswith(extension):
            distribution, separator, file_version = filename[:-len(extension)].rpartition("-")
            return bool(separator and _normalize(distribution) == name and file_version == version)
    return False


def simple_published_at(registry: str, name: str, version: str, transport: SimpleIndexTransport) -> datetime:
    """Require upload times for all matching files and use the newest upload.

    Unlike a first-release date, this conservative cutoff also covers a wheel
    added to an old version. No file URL from the response is ever requested.
    """
    registry = canonical_python_index(registry)
    if not registry or not _NAME.fullmatch(name) or not re.fullmatch(r"[A-Za-z0-9.!+_-]+", version):
        raise PythonIndexError("unsupported Python index package identity")
    normalized = _normalize(name)
    data = transport.get_json(f"{registry}/{normalized}/", headers=_headers(registry))
    if (not isinstance(data, dict) or not isinstance(data.get("meta"), dict)
            or not isinstance(data.get("name"), str) or _normalize(data["name"]) != normalized
            or not isinstance(data.get("files"), list)):
        raise PythonIndexError("Python index metadata has invalid project identity or structure")
    api_version = data["meta"].get("api-version")
    if not isinstance(api_version, str) or not re.fullmatch(r"1\.[0-9]{1,6}", api_version) or int(api_version.split(".")[1]) < 1:
        raise PythonIndexError("Python index requires Simple JSON API 1.1 or newer in major version 1")
    stamps = []
    for item in data["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            raise PythonIndexError("Python index metadata contains an invalid file entry")
        if not _file_matches(item["filename"], normalized, version):
            continue
        stamp = item.get("upload-time")
        if not isinstance(stamp, str) or not _STAMP.fullmatch(stamp):
            raise PythonIndexError("Python index version has missing or invalid PEP 700 upload-time")
        try:
            stamps.append(datetime.fromisoformat(stamp[:-1] + "+00:00").astimezone(timezone.utc))
        except ValueError:
            raise PythonIndexError("Python index version has invalid PEP 700 upload-time") from None
    if not stamps:
        raise PythonIndexError("Python index has no verifiable files for this exact name and version")
    return max(stamps)
