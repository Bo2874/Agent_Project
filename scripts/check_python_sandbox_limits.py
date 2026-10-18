import asyncio
import sys
from dataclasses import dataclass

from agent_core.code_execution import (
    DEFAULT_PYTHON_EXECUTION_LIMITS,
    DockerPythonSandbox,
    PythonExecutionRequest,
    PythonExecutionResult,
    PythonExecutionStatus,
    SandboxUnavailableError,
)


@dataclass(frozen=True, slots=True)
class LimitCheck:
    name: str
    code: str
    allowed_statuses: frozenset[PythonExecutionStatus]
    expected_output: str = ""
    output_must_be_truncated: bool = False


CHECKS = (
    LimitCheck(
        name="wall_time",
        code="import time\ntime.sleep(10)",
        allowed_statuses=frozenset({PythonExecutionStatus.TIMED_OUT}),
    ),
    LimitCheck(
        name="cpu_time",
        code="while True:\n    pass",
        allowed_statuses=frozenset({PythonExecutionStatus.RESOURCE_EXHAUSTED}),
    ),
    LimitCheck(
        name="memory",
        code=(
            "try:\n"
            "    value = bytearray(256 * 1024 * 1024)\n"
            "except MemoryError:\n"
            "    print('MEMORY_BLOCKED')\n"
            "else:\n"
            "    print('MEMORY_UNEXPECTED', len(value))"
        ),
        allowed_statuses=frozenset(
            {PythonExecutionStatus.COMPLETED, PythonExecutionStatus.RESOURCE_EXHAUSTED}
        ),
        expected_output="MEMORY_BLOCKED",
    ),
    LimitCheck(
        name="process",
        code=(
            "import subprocess, sys\n"
            "try:\n"
            "    subprocess.run([sys.executable, '-c', 'print(1)'], check=True)\n"
            "except (OSError, subprocess.SubprocessError):\n"
            "    print('PROCESS_BLOCKED')\n"
            "else:\n"
            "    print('PROCESS_UNEXPECTED')"
        ),
        allowed_statuses=frozenset({PythonExecutionStatus.COMPLETED}),
        expected_output="PROCESS_BLOCKED",
    ),
    LimitCheck(
        name="output",
        code="print('x' * 100_000)",
        allowed_statuses=frozenset({PythonExecutionStatus.RESOURCE_EXHAUSTED}),
        output_must_be_truncated=True,
    ),
)


def check_passed(check: LimitCheck, result: PythonExecutionResult) -> bool:
    if result.status not in check.allowed_statuses:
        return False
    if check.expected_output and result.status is PythonExecutionStatus.COMPLETED:
        if check.expected_output not in result.stdout:
            return False
        if "UNEXPECTED" in result.stdout:
            return False
    if check.output_must_be_truncated and not result.output_truncated:
        return False
    output_bytes = len(result.stdout.encode()) + len(result.stderr.encode())
    return output_bytes <= DEFAULT_PYTHON_EXECUTION_LIMITS.max_output_bytes


async def run_checks() -> int:
    sandbox = DockerPythonSandbox()
    if not await sandbox.image_available():
        print("Sandbox image chưa tồn tại hoặc Docker Desktop chưa chạy.")
        return 1

    all_passed = True
    for check in CHECKS:
        result = await sandbox.execute(PythonExecutionRequest(code=check.code))
        passed = check_passed(check, result)
        all_passed = all_passed and passed
        print(
            f"{check.name}: {'PASSED' if passed else 'FAILED'} "
            f"(status={result.status.value}, duration_ms={result.duration_ms})"
        )
    print("SANDBOX_LIMITS_PASSED" if all_passed else "SANDBOX_LIMITS_FAILED")
    return 0 if all_passed else 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        raise SystemExit(asyncio.run(run_checks()))
    except SandboxUnavailableError as exc:
        print(f"Không thể chạy sandbox: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
