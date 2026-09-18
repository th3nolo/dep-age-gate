"""Build sdist/wheel offline and exercise an isolated wheel installation."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def run(args, cwd, expected=0):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode != expected:
        raise RuntimeError(f"{args!r}: expected exit {expected}, got {result.returncode}")
    return result


def main():
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("ALLOW_YOUNG_DEPS", None)
    os.environ["UV_OFFLINE"] = "1"
    os.environ["UV_NO_CONFIG"] = "1"
    with tempfile.TemporaryDirectory(prefix="dep-age-package-") as directory:
        scratch = Path(directory)
        dist = scratch / "dist"
        run(["uv", "build", "--python", sys.executable, "--no-build-isolation",
             "--out-dir", str(dist)], ROOT)
        wheels = list(dist.glob("*.whl"))
        if len(wheels) != 1 or len(list(dist.glob("*.tar.gz"))) != 1:
            raise RuntimeError("Expected exactly one wheel and one source distribution")
        environment = scratch / "installed"
        run(["uv", "venv", "--python", sys.executable, "--no-python-downloads",
             str(environment)], scratch)
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        cli = binaries / ("dep-age-gate.exe" if os.name == "nt" else "dep-age-gate")
        run(["uv", "pip", "install", "--python", str(python), "--no-deps",
             str(wheels[0])], scratch)
        run([str(python), "-I", "-c",
             "from pathlib import Path; import dep_age_gate, sys; "
             "assert Path(dep_age_gate.__file__).is_relative_to(Path(sys.prefix))"], scratch)
        run([str(cli), "--version"], scratch)
        run([str(cli), "--help"], scratch)
        run([str(python), "-I", "-m", "dep_age_gate", "--help"], scratch)
        project = scratch / "pyproject.toml"
        project.write_text('[project]\nname = "smoke"\nversion = "0.0.0"\n', encoding="utf-8")
        run([str(cli), "init-uv", str(project), "--write"], scratch)
        if 'exclude-newer = "72h"' not in project.read_text(encoding="utf-8"):
            raise RuntimeError("Installed CLI did not write the requested age gate")
        requirements = scratch / "requirements.txt"
        requirements.write_text("-e .\n", encoding="utf-8")
        failure = run([str(cli), "audit", "--all", str(requirements), "--json"],
                      scratch, expected=1)
        import json
        if not json.loads(failure.stdout)["errors"]:
            raise RuntimeError("Installed CLI did not report incomplete dependency coverage")


if __name__ == "__main__":
    main()
