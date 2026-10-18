from pathlib import Path

from agent_core.code_execution import DockerPythonSandbox
from backend.dependencies_tools import get_tool_registry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_sandbox_command_keeps_all_security_boundaries_enabled() -> None:
    command = DockerPythonSandbox().build_run_command("security-test")
    security_options = [
        command[index + 1] for index, value in enumerate(command) if value == "--security-opt"
    ]

    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--pids-limit") + 1] == "2"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--user") + 1] == "65532:65532"
    assert command[command.index("--memory-swap") + 1] == command[command.index("--memory") + 1]
    assert "--read-only" in command
    assert security_options == ["no-new-privileges", "seccomp=builtin"]
    assert not {"--privileged", "--mount", "--volume", "--env", "--env-file"} & set(command)


def test_sandbox_image_does_not_copy_project_or_declare_secrets() -> None:
    dockerfile = (PROJECT_ROOT / "infra/docker/python-sandbox/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "COPY ." not in dockerfile
    assert "DB_PASSWORD" not in dockerfile
    assert "DEEPSEEK_API_KEY" not in dockerfile
    assert "AUTH_SECRET_KEY" not in dockerfile


def test_production_registry_keeps_python_tool_approval_gate() -> None:
    registry = get_tool_registry()
    registered = registry.get("run_python_code")

    assert registry.is_allowlisted("run_python_code") is True
    assert registered.policy.requires_approval is True
    assert registered.policy.idempotent is False
    assert registered.policy.max_retries == 0
