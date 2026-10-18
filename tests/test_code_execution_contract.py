import hashlib
from dataclasses import FrozenInstanceError

import pytest
from jsonschema.validators import validator_for

from agent_core.code_execution import (
    DEFAULT_PYTHON_EXECUTION_LIMITS,
    RUN_PYTHON_CODE_POLICY,
    PythonExecutionArtifact,
    PythonExecutionLimits,
    PythonExecutionRequest,
    PythonExecutionResult,
    PythonExecutionStatus,
    RunPythonCodeInput,
)
from backend.dependencies_tools import get_tool_registry


def test_run_python_code_has_pydantic_bounded_strict_input_schema() -> None:
    schema = RunPythonCodeInput.model_json_schema()
    validator_for(schema).check_schema(schema)

    assert schema["required"] == ["code"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["code"]["maxLength"] == 20_000
    assert schema["properties"]["stdin"]["maxLength"] == 10_000


def test_run_python_code_requires_approval_and_never_retries() -> None:
    assert RUN_PYTHON_CODE_POLICY.requires_approval is True
    assert RUN_PYTHON_CODE_POLICY.idempotent is False
    assert RUN_PYTHON_CODE_POLICY.max_retries == 0
    assert RUN_PYTHON_CODE_POLICY.allowed_roles == frozenset({"user", "admin"})


def test_default_limits_disable_network_and_bound_resources() -> None:
    limits = DEFAULT_PYTHON_EXECUTION_LIMITS

    assert limits.network_enabled is False
    assert limits.wall_timeout_seconds == 5
    assert limits.cpu_time_seconds == 2
    assert limits.max_cpu_cores == 1
    assert limits.memory_bytes == 128 * 1024 * 1024
    assert limits.max_output_bytes == 64 * 1024
    assert limits.max_artifact_count == 3
    assert limits.max_artifact_bytes == 8 * 1024
    assert limits.max_total_artifact_bytes == 16 * 1024
    assert limits.max_processes == 1
    assert limits.max_filesystem_bytes == 10 * 1024 * 1024
    with pytest.raises(FrozenInstanceError):
        limits.network_enabled = True  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"network_enabled": True},
        {"wall_timeout_seconds": 31},
        {"cpu_time_seconds": 6, "wall_timeout_seconds": 5},
        {"max_cpu_cores": 2.1},
        {"memory_bytes": 513 * 1024 * 1024},
        {"max_output_bytes": 1_048_577},
        {"max_artifact_count": 11},
        {"max_artifact_bytes": 17_000, "max_total_artifact_bytes": 16_000},
        {"max_total_artifact_bytes": 512 * 1024 + 1},
        {"max_processes": 9},
    ],
)
def test_limits_reject_unsafe_configuration(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PythonExecutionLimits(**overrides)  # type: ignore[arg-type]


def test_request_preserves_source_and_validates_utf8_byte_limits() -> None:
    code = "  print('xin chào')\n"
    request = PythonExecutionRequest.from_arguments({"code": code, "stdin": "dữ liệu"})

    assert request.code == code
    assert request.stdin == "dữ liệu"

    tiny_limits = PythonExecutionLimits(max_source_bytes=4, max_stdin_bytes=4)
    with pytest.raises(ValueError, match="code vượt"):
        PythonExecutionRequest.from_arguments({"code": "print('á')"}, limits=tiny_limits)
    with pytest.raises(ValueError, match="stdin vượt"):
        PythonExecutionRequest.from_arguments(
            {"code": "1", "stdin": "ááá"},
            limits=tiny_limits,
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"code": "   "},
        {"code": "print(1)\x00"},
        {"code": "print(1)", "path": "C:/secret"},
        {"code": 123},
    ],
)
def test_request_rejects_invalid_or_expanded_input(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PythonExecutionRequest.from_arguments(arguments)


def test_completed_execution_converts_to_successful_tool_result() -> None:
    artifact_content = "# Báo cáo\n"
    artifact = PythonExecutionArtifact(
        name="report.md",
        media_type="text/markdown",
        size_bytes=len(artifact_content.encode()),
        sha256=hashlib.sha256(artifact_content.encode()).hexdigest(),
        content=artifact_content,
    )
    execution = PythonExecutionResult(
        status=PythonExecutionStatus.COMPLETED,
        stdout="6\n",
        exit_code=0,
        duration_ms=12,
        artifacts=(artifact,),
        artifact_warnings=("artifact_rejected_media_type",),
    )

    result = execution.to_tool_result()

    assert result.is_error is False
    assert result.data["status"] == "completed"
    assert result.data["stdout"] == "6\n"
    assert result.data["exit_code"] == 0
    assert result.data["output_trust"] == "untrusted"
    assert result.data["artifacts"][0]["name"] == "report.md"
    assert result.data["artifact_warnings"] == ["artifact_rejected_media_type"]


@pytest.mark.parametrize(
    ("name", "media_type", "content"),
    [
        ("../report.txt", "text/plain", "safe"),
        ("report.html", "text/html", "<b>unsafe</b>"),
        ("report.json", "application/json", "not json"),
        ("report.txt", "text/plain", "bad\x00text"),
    ],
)
def test_artifact_rejects_unsafe_name_type_or_content(
    name: str,
    media_type: str,
    content: str,
) -> None:
    with pytest.raises((ValueError, TypeError)):
        PythonExecutionArtifact(
            name=name,
            media_type=media_type,
            size_bytes=len(content.encode()),
            sha256=hashlib.sha256(content.encode()).hexdigest(),
            content=content,
        )


def test_artifact_rejects_wrong_checksum() -> None:
    with pytest.raises(ValueError, match="Checksum"):
        PythonExecutionArtifact(
            name="report.txt",
            media_type="text/plain",
            size_bytes=4,
            sha256="0" * 64,
            content="safe",
        )


def test_failed_execution_cannot_return_partial_artifact() -> None:
    content = "partial"
    artifact = PythonExecutionArtifact(
        name="partial.txt",
        media_type="text/plain",
        size_bytes=len(content),
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        content=content,
    )

    with pytest.raises(ValueError, match="completed"):
        PythonExecutionResult(
            status=PythonExecutionStatus.FAILED,
            exit_code=1,
            artifacts=(artifact,),
        )


def test_timeout_execution_converts_to_bounded_failure() -> None:
    execution = PythonExecutionResult(
        status=PythonExecutionStatus.TIMED_OUT,
        stderr="Execution stopped",
        duration_ms=5_000,
        output_truncated=True,
    )

    result = execution.to_tool_result()

    assert result.is_error is True
    assert result.error_code == "python_timed_out"
    assert result.data["exit_code"] is None
    assert result.data["output_truncated"] is True


@pytest.mark.parametrize(
    "result",
    [
        {"status": PythonExecutionStatus.COMPLETED, "exit_code": 1},
        {"status": PythonExecutionStatus.FAILED, "exit_code": 0},
        {"status": PythonExecutionStatus.FAILED, "exit_code": None},
        {"status": PythonExecutionStatus.TIMED_OUT, "exit_code": -9},
        {"status": "completed", "exit_code": 0},
    ],
)
def test_result_rejects_inconsistent_state(result: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PythonExecutionResult(**result)  # type: ignore[arg-type]


def test_tool_is_registered_after_sandbox_attack_tests_pass() -> None:
    registry = get_tool_registry()
    registered = registry.get("run_python_code")

    assert "run_python_code" in registry
    assert registry.can_access("run_python_code", "user") is True
    assert registered.tool.args_schema is RunPythonCodeInput
    assert registered.policy.requires_approval is True
    assert registered.policy.max_retries == 0
