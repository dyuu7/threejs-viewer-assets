"""Rebuild offline, install into a fresh environment, and optionally test Chromium."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from project import ROOT, require, validate_source
from validate import validate_distributions, wheel_files


def run(command, **kwargs):
    print("+ " + " ".join(map(str, command)), flush=True)
    subprocess.run([str(arg) for arg in command], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    before = validate_source(modules=True)
    wheel, sdist = validate_distributions(args.dist.resolve())
    with tempfile.TemporaryDirectory(prefix="threejs-check-") as temporary:
        work = Path(temporary)
        rebuilt = work / "rebuilt"
        run(
            [
                "uv",
                "build",
                "--wheel",
                "--offline",
                "--no-build-isolation",
                "--out-dir",
                rebuilt,
                sdist,
            ],
            cwd=ROOT,
        )
        require(
            wheel_files(wheel) == wheel_files(next(rebuilt.glob("*.whl"))), "sdist rebuild differs"
        )
        environment = work / "installed"
        run(["uv", "venv", "--no-project", "--python", sys.executable, environment], cwd=work)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(["uv", "pip", "install", "--python", python, "--offline", "--no-deps", wheel], cwd=work)
        # Installed resource access works with no Node/npm executable on PATH,
        # no source checkout on sys.path, and no dependencies in the new venv.
        isolated_env = dict(os.environ, PATH="")
        isolated_env.pop("PYTHONPATH", None)
        run(
            [python, "-I", ROOT / "scripts" / "validate.py", "--installed"],
            cwd=work,
            env=isolated_env,
        )
        if args.browser:
            browser_env = dict(os.environ, THREEJS_INSTALLED_PYTHON=str(python))
            run(
                [sys.executable, "-m", "pytest", "tests/test_browser.py", "-v"],
                cwd=ROOT,
                env=browser_env,
            )
    require(before == validate_source(), "Verification changed source resources")
    print("Offline sdist rebuild and isolated wheel installation passed.")


if __name__ == "__main__":
    main()
