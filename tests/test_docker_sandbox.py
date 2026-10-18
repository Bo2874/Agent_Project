import hashlib
import json
from pathlib import Path

import pytest

from agent_core.code_execution import (
    DEFAULT_PYTHON_EXECUTION_LIMITS,
    DockerPythonSandbox,
    DockerSandboxConfig,
    PythonExecutionStatus,
    SandboxProtocolError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_docker_command_uses_disposable_networkless_read_only_container() -> None:
    sandbox = DockerPythonSandbox()

    command = sandbox.build_run_command("sandbox-test")

    assert command[:3] == ("docker", "run", "--rm")
    assert command[-1] == "personal-agent-python-sandbox:3.11-v1"
    assert ("--network", "none") == command[command.index("--network") :][:2]
    assert "--read-only" in command
    assert ("--user", "65532:65532") == command[command.index("--user") :][:2]
    assert ("--cap-drop", "ALL") == command[command.index("--cap-drop") :][:2]
    assert "no-new-privileges" in command
    assert "--rm" in command
    assert "--mount" not in command
    assert "--volume" not in command
    assert "-v" not in command


def test_docker_command_allocates_only_bounded_temporary_filesystems() -> None:
    sandbox = DockerPythonSandbox()

    command = sandbox.build_run_command("sandbox-test")
    tmpfs_values = [command[index + 1] for index, value in enumerate(command) if value == "--tmpfs"]

    assert len(tmpfs_values) == 2
    assert tmpfs_values[0].startswith("/workspace:rw,noexec,nosuid,nodev,size=8388608")
    assert tmpfs_values[1].startswith("/tmp:rw,noexec,nosuid,nodev,size=2097152")
    assert all("uid=65532,gid=65532" in value for value in tmpfs_values)
    assert (
        sum(
            int(value.split("size=", maxsplit=1)[1].split(",", maxsplit=1)[0])
            for value in tmpfs_values
        )
        == DEFAULT_PYTHON_EXECUTION_LIMITS.max_filesystem_bytes
    )


def test_docker_command_applies_cpu_memory_process_and_file_limits() -> None:
    command = DockerPythonSandbox().build_run_command("sandbox-test")
    ulimit_values = [
        command[index + 1] for index, value in enumerate(command) if value == "--ulimit"
    ]

    assert command[command.index("--cpus") + 1] == "1"
    assert command[command.index("--memory") + 1] == str(128 * 1024 * 1024)
    assert command[command.index("--memory-swap") + 1] == str(128 * 1024 * 1024)
    # One trusted supervisor plus one user-code process; user code cannot create a third PID.
    assert command[command.index("--pids-limit") + 1] == "2"
    assert ulimit_values == [
        "cpu=2:2",
        "nofile=32:32",
        f"fsize={10 * 1024 * 1024}:{10 * 1024 * 1024}",
    ]


@pytest.mark.parametrize("image", ["", "-danger", "image name", "a" * 256])
def test_config_rejects_unsafe_image_name(image: str) -> None:
    with pytest.raises(ValueError):
        DockerSandboxConfig(image=image)


def test_config_rejects_empty_docker_executable() -> None:
    with pytest.raises(ValueError):
        DockerSandboxConfig(docker_executable="  ")


@pytest.mark.asyncio
async def test_image_is_unavailable_when_docker_executable_is_missing() -> None:
    sandbox = DockerPythonSandbox(
        DockerSandboxConfig(docker_executable="missing-docker-command-for-sandbox-test")
    )

    assert await sandbox.image_available() is False


def test_decodes_valid_runner_protocol() -> None:
    payload = json.dumps(
        {
            "status": "completed",
            "stdout": "xin chao\n",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 14,
            "output_truncated": False,
            "artifacts": [],
            "artifact_warnings": [],
        }
    ).encode()

    result = DockerPythonSandbox()._decode_result(payload)

    assert result.status is PythonExecutionStatus.COMPLETED
    assert result.stdout == "xin chao\n"


@pytest.mark.parametrize(
    "payload",
    [b"not-json", b"[]", b'{"status":"completed"}', b'{"status":"unknown"}'],
)
def test_rejects_invalid_runner_protocol(payload: bytes) -> None:
    with pytest.raises(SandboxProtocolError):
        DockerPythonSandbox()._decode_result(payload)


def test_host_rejects_decoded_output_above_configured_limit() -> None:
    payload = json.dumps(
        {
            "status": "completed",
            "stdout": "x" * (DEFAULT_PYTHON_EXECUTION_LIMITS.max_output_bytes + 1),
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 14,
            "output_truncated": False,
            "artifacts": [],
            "artifact_warnings": [],
        }
    ).encode()

    with pytest.raises(SandboxProtocolError):
        DockerPythonSandbox()._decode_result(payload)


def test_host_rejects_artifact_above_configured_limit() -> None:
    content = "x" * (DEFAULT_PYTHON_EXECUTION_LIMITS.max_artifact_bytes + 1)
    payload = json.dumps(
        {
            "status": "completed",
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 14,
            "output_truncated": False,
            "artifacts": [
                {
                    "name": "large.txt",
                    "media_type": "text/plain",
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "content": content,
                }
            ],
            "artifact_warnings": [],
        }
    ).encode()

    with pytest.raises(SandboxProtocolError):
        DockerPythonSandbox()._decode_result(payload)


def test_sandbox_image_is_non_root_and_has_no_dependency_install_step() -> None:
    dockerfile = (PROJECT_ROOT / "infra/docker/python-sandbox/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert dockerfile.startswith("FROM python:3.11.16-slim-bookworm\n")
    assert "USER 65532:65532" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "pip install" not in dockerfile
    assert "COPY ." not in dockerfile
