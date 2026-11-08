import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_default_compose_does_not_advertise_python_without_its_broker():
    docker = os.environ.get("ARTIFACT_DOCKER") or shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose CLI required")
    root = Path(__file__).resolve().parents[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMFILES",
            "PROGRAMDATA",
            "DOCKER_CONFIG",
        }
    }
    result = subprocess.run(
        [docker, "compose", "--env-file", ".env.docker.example", "config", "--format", "json"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(result.stdout)
    broker = config["services"].get("artifact-runner")
    available_by_default = broker is not None and not broker.get("profiles")
    enabled = config["services"]["app"]["environment"]["PYTHON_SANDBOX_ENABLED"]
    assert available_by_default or enabled == "false"
