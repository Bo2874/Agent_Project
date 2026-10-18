from collections.abc import Mapping
from typing import Any, Protocol

from agent_core.code_execution.contract import (
    DEFAULT_PYTHON_EXECUTION_LIMITS,
    RUN_PYTHON_CODE_DESCRIPTION,
    RUN_PYTHON_CODE_POLICY,
    PythonExecutionRequest,
    PythonExecutionResult,
    RunPythonCodeInput,
)
from agent_core.code_execution.docker_sandbox import (
    DockerPythonSandbox,
    SandboxProtocolError,
    SandboxUnavailableError,
)
from agent_core.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    create_context_tool,
)


class PythonSandbox(Protocol):
    async def execute(self, request: PythonExecutionRequest) -> PythonExecutionResult: ...


class RunPythonCodeHandler:
    """Bridge the approved tool call to the isolated Python sandbox."""

    def __init__(self, sandbox: PythonSandbox | None = None) -> None:
        self.sandbox = sandbox or DockerPythonSandbox()

    async def __call__(
        self,
        _context: ToolContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        try:
            request = PythonExecutionRequest.from_arguments(
                arguments,
                limits=DEFAULT_PYTHON_EXECUTION_LIMITS,
            )
            result = await self.sandbox.execute(request)
        except ValueError as exc:
            return ToolResult.failure(
                str(exc),
                error_code="tool_schema_validation_failed",
            )
        except SandboxUnavailableError:
            return ToolResult.failure(
                "Python sandbox không khả dụng.",
                error_code="tool_unavailable",
            )
        except SandboxProtocolError:
            return ToolResult.failure(
                "Python sandbox trả về dữ liệu không hợp lệ.",
                error_code="python_sandbox_protocol_error",
            )
        return result.to_tool_result()


def register_python_execution_tool(
    registry: ToolRegistry,
    *,
    sandbox: PythonSandbox | None = None,
) -> None:
    tool = create_context_tool(
        name="run_python_code",
        description=RUN_PYTHON_CODE_DESCRIPTION,
        args_schema=RunPythonCodeInput,
        coroutine=RunPythonCodeHandler(sandbox),
    )
    registry.register(
        tool,
        policy=RUN_PYTHON_CODE_POLICY,
        routing=ToolRoutingPolicy(
            keywords=frozenset(
                {
                    "chạy python",
                    "python code",
                    "tính bằng python",
                    "phân tích dữ liệu",
                    "run code",
                }
            )
        ),
    )
