import asyncio
import sys

from agent_core.code_execution import (
    DockerPythonSandbox,
    PythonExecutionRequest,
    PythonExecutionStatus,
    SandboxUnavailableError,
)


async def run_check() -> int:
    sandbox = DockerPythonSandbox()
    if not await sandbox.image_available():
        print("Sandbox image chưa tồn tại hoặc Docker Desktop chưa chạy.")
        print(
            "Build bằng: docker build --tag personal-agent-python-sandbox:3.11-v1 "
            "infra/docker/python-sandbox"
        )
        return 1

    first_result = await sandbox.execute(
        PythonExecutionRequest(
            code="""
from pathlib import Path
import socket

Path('/workspace/probe.txt').write_text('temporary', encoding='utf-8')
print('TEMP_WRITE_OK')

try:
    Path('/opt/sandbox/probe.txt').write_text('blocked', encoding='utf-8')
except OSError:
    print('ROOT_READ_ONLY')
else:
    print('ROOT_WRITE_UNEXPECTED')

try:
    socket.create_connection(('1.1.1.1', 53), timeout=0.5)
except OSError:
    print('NETWORK_BLOCKED')
else:
    print('NETWORK_UNEXPECTED')
""".strip()
        )
    )
    second_result = await sandbox.execute(
        PythonExecutionRequest(
            code=(
                "from pathlib import Path\n"
                "print('WORKSPACE_CLEAN' if not Path('/workspace/probe.txt').exists() "
                "else 'WORKSPACE_LEAK')"
            )
        )
    )

    expected_first = {"TEMP_WRITE_OK", "ROOT_READ_ONLY", "NETWORK_BLOCKED"}
    first_lines = set(first_result.stdout.splitlines())
    passed = (
        first_result.status is PythonExecutionStatus.COMPLETED
        and second_result.status is PythonExecutionStatus.COMPLETED
        and expected_first <= first_lines
        and second_result.stdout.strip() == "WORKSPACE_CLEAN"
    )
    print(first_result.stdout, end="")
    print(second_result.stdout, end="")
    print("SANDBOX_CHECK_PASSED" if passed else "SANDBOX_CHECK_FAILED")
    return 0 if passed else 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        raise SystemExit(asyncio.run(run_check()))
    except SandboxUnavailableError as exc:
        print(f"Không thể chạy sandbox: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
