"""Negative-path checks for the CI runner, using disposable synthetic suites."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from run_tests import REQUIRED_MODULES


class RunnerTests(unittest.TestCase):
    def run_suite(self, mode):
        with tempfile.TemporaryDirectory(prefix="dep-age-guard-") as directory:
            root = Path(directory)
            scripts = root / ".github" / "scripts"
            scripts.mkdir(parents=True)
            runner = scripts / "run_tests.py"
            shutil.copyfile(Path(__file__).with_name("run_tests.py"), runner)
            tests = root / "tests"
            tests.mkdir()
            for name in REQUIRED_MODULES:
                source = "def test_present():\n    assert True\n"
                if name == "test_audit.py":
                    source += (
                        "import pytest\n"
                        "@pytest.mark.parametrize('case', range(237))\n"
                        "def test_cases(case):\n    assert case >= 0\n"
                    )
                (tests / name).write_text(source, encoding="utf-8")
            target = tests / "test_cli.py"
            if mode == "empty":
                for path in tests.iterdir():
                    path.write_text("", encoding="utf-8")
            elif mode == "missing-module":
                target.rename(tests / "test_unrelated.py")
            elif mode == "syntax-error":
                target.write_text("def invalid(\n", encoding="utf-8")
            elif mode in {"skip", "xfail"}:
                target.write_text(
                    f"import pytest\ndef test_present():\n    pytest.{mode}('probe')\n",
                    encoding="utf-8",
                )
            elif mode == "network":
                target.write_text(
                    "import socket\ndef test_present():\n"
                    "    try:\n        socket.getaddrinfo('example.invalid', 443)\n"
                    "    except AssertionError:\n        pass\n",
                    encoding="utf-8",
                )
            return subprocess.run([sys.executable, str(runner)], cwd=root,
                                  text=True, capture_output=True, timeout=60)

    def test_complete_suite_passes(self):
        result = self.run_suite("complete")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("245 passed", result.stdout)

    def test_incomplete_or_networked_suites_fail(self):
        for mode, evidence in {
            "empty": "Incomplete suite",
            "missing-module": "missing modules: ['test_cli.py']",
            "syntax-error": "SyntaxError",
            "skip": "CI requires every collected test",
            "xfail": "CI requires every collected test",
            "network": "Test attempted external network access",
        }.items():
            with self.subTest(mode=mode):
                result = self.run_suite(mode)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(evidence, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
