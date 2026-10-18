import hashlib
from uuid import uuid4

import pytest

from agent_core.code_execution import (
    PythonExecutionArtifact,
    PythonExecutionRequest,
    PythonExecutionResult,
    PythonExecutionStatus,
    register_python_execution_tool,
)
from agent_core.tools import ToolContext, ToolExecutor, ToolRegistry


class FakeSandbox:
    def __init__(self, result: PythonExecutionResult) -> None:
        self.result = result
        self.requests: list[PythonExecutionRequest] = []

    async def execute(self, request: PythonExecutionRequest) -> PythonExecutionResult:
        self.requests.append(request)
        return self.result


def make_context() -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_role="user",
        run_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_python_tool_requires_approval_before_sandbox_execution() -> None:
    sandbox = FakeSandbox(
        PythonExecutionResult(
            status=PythonExecutionStatus.COMPLETED,
            exit_code=0,
        )
    )
    registry = ToolRegistry()
    register_python_execution_tool(registry, sandbox=sandbox)

    result = await ToolExecutor().execute(
        registry,
        make_context(),
        "run_python_code",
        {"code": "print(1)"},
    )

    assert result.error_code == "tool_approval_required"
    assert sandbox.requests == []


@pytest.mark.asyncio
async def test_python_tool_returns_checked_output_and_artifact_to_agent() -> None:
    content = '{"answer": 6}'
    artifact = PythonExecutionArtifact(
        name="answer.json",
        media_type="application/json",
        size_bytes=len(content.encode()),
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        content=content,
    )
    sandbox = FakeSandbox(
        PythonExecutionResult(
            status=PythonExecutionStatus.COMPLETED,
            stdout="6\n",
            stderr="warning\n",
            exit_code=0,
            artifacts=(artifact,),
        )
    )
    registry = ToolRegistry()
    register_python_execution_tool(registry, sandbox=sandbox)

    result = await ToolExecutor().execute(
        registry,
        make_context(),
        "run_python_code",
        {"code": "print(6)"},
        approval_granted=True,
    )

    assert result.is_error is False
    assert result.data["stdout"] == "6\n"
    assert result.data["stderr"] == "warning\n"
    assert result.data["output_trust"] == "untrusted"
    assert result.data["artifacts"] == [artifact.to_dict()]
    assert len(sandbox.requests) == 1
