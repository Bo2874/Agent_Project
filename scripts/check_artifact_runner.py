"""Offline Docker gate: transport, signatures, isolation, replay and cancellation.

No model, application database or external service is contacted. Requires locally
built personal-agent-artifact-code:1. Prints NOT RUN and exits nonzero if unavailable.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    docker = os.environ.get("ARTIFACT_DOCKER") or shutil.which("docker")
    if not docker:
        print("NOT RUN: Docker CLI unavailable")
        return 2
    probe = subprocess.run(
        [docker, "image", "inspect", "personal-agent-artifact-code:1"],
        capture_output=True,
        check=False,
    )
    if probe.returncode:
        print("NOT RUN: build the artifact code sandbox image first")
        return 2
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="artifact-runner-gate-") as temporary:
        environment = {
            **os.environ,
            "ARTIFACT_RUNNER_DOCKER": "1",
            "ARTIFACT_DOCKER": docker,
            "PYTHONPATH": str(root),
        }
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--basetemp={temporary}/pytest",
                "tests/test_artifact_runner.py",
            ],
            cwd=root,
            env=environment,
            check=False,
        )
    if result.returncode == 0:
        print("PASS: real Docker isolation, binary transport, HMAC, replay, cancel and cleanup")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
