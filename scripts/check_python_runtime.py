"""Offline acceptance of the one Python image; no paid model or real database."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    docker = os.environ.get("ARTIFACT_DOCKER") or shutil.which("docker")
    if not docker:
        print("NOT RUN: Docker CLI unavailable")
        return 2
    if subprocess.run(
        [docker, "image", "inspect", "personal-agent-python-runtime:1"], capture_output=True
    ).returncode:
        print("NOT RUN: build personal-agent-python-runtime:1 first")
        return 2
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="python-runtime-qa-") as folder:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                f"--basetemp={folder}/pytest",
                "tests/test_unified_python_runtime.py",
                "tests/test_unified_python_inspection.py",
                "tests/test_unified_python_docker.py",
                "tests/test_artifact_runner.py",
            ],
            cwd=root,
            env={**os.environ, "ARTIFACT_RUNNER_DOCKER": "1", "ARTIFACT_DOCKER": docker},
            check=False,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
