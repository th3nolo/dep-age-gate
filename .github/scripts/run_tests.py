"""Run the entire offline suite, rejecting missing coverage and skipped tests."""

import ipaddress
import os
from pathlib import Path
import shutil
import socket
import sys

os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ.pop("PYTEST_ADDOPTS", None)

import pytest


ROOT = Path(__file__).resolve().parents[2]
MINIMUM_TESTS = 245
REQUIRED_MODULES = {
    "test_audit.py", "test_cli.py", "test_duration.py", "test_parsers.py",
    "test_registry.py", "test_registry_sources.py", "test_spec.py", "test_uvinit.py",
}


def require_loopback(host):
    """Permit local HTTP fixtures, never external DNS or registry traffic."""
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise AssertionError(f"External network is forbidden in unit tests: {host}")


class SuiteGuard:
    @pytest.fixture(autouse=True)
    def offline_network(self, monkeypatch):
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_getaddrinfo = socket.getaddrinfo
        denied = []

        def check(host):
            try:
                require_loopback(host)
            except AssertionError:
                denied.append(host)
                raise

        def connect(sock, address):
            check(address[0])
            return original_connect(sock, address)

        def connect_ex(sock, address):
            check(address[0])
            return original_connect_ex(sock, address)

        def getaddrinfo(host, *args, **kwargs):
            check(host)
            return original_getaddrinfo(host, *args, **kwargs)

        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
        yield
        if denied:
            pytest.fail(f"Test attempted external network access: {denied}")

    def pytest_collection_finish(self, session):
        modules = {item.path.name for item in session.items}
        missing = REQUIRED_MODULES - modules
        if missing or len(session.items) < MINIMUM_TESTS:
            raise pytest.UsageError(
                f"Incomplete suite: {len(session.items)} tests (minimum {MINIMUM_TESTS}); "
                f"missing modules: {sorted(missing)}"
            )

    def pytest_sessionfinish(self, session, exitstatus):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        incomplete = sum(
            len(reporter.stats.get(outcome, []))
            for outcome in ("skipped", "xfailed", "xpassed", "deselected")
        )
        if incomplete:
            reporter.write_sep("!", "CI requires every collected test to run and pass")
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


if __name__ == "__main__":
    if shutil.which("git") is None:
        raise SystemExit("Git is required for audit regression tests")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    raise SystemExit(pytest.main(["tests", "-ra", "--strict-config", "--strict-markers"],
                                plugins=[SuiteGuard()]))
