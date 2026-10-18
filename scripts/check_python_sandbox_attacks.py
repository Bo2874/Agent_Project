import asyncio
import os
import sys
from dataclasses import dataclass

from agent_core.code_execution import (
    DockerPythonSandbox,
    PythonExecutionRequest,
    PythonExecutionStatus,
    SandboxUnavailableError,
)


@dataclass(frozen=True, slots=True)
class AttackProbe:
    name: str
    code: str
    expected_marker: str


PROBES = (
    AttackProbe(
        name="secret_read",
        expected_marker="SECRET_BLOCKED",
        code="""
import os
from pathlib import Path

sensitive_names = {
    'DB_PASSWORD', 'DEEPSEEK_API_KEY', 'AUTH_SECRET_KEY',
    'SANDBOX_HOST_SECRET_CANARY'
}
visible_names = set(os.environ)
try:
    init_environment = Path('/proc/1/environ').read_bytes().split(b'\\0')
    visible_names.update(
        item.split(b'=', 1)[0].decode('utf-8', errors='ignore')
        for item in init_environment if b'=' in item
    )
except OSError:
    pass

secret_paths = (
    Path('/workspace/.env'), Path('/app/.env'), Path('/host/.env'),
    Path('/run/secrets'), Path('/var/run/docker.sock')
)
leaked = bool(sensitive_names & visible_names) or any(path.exists() for path in secret_paths)
print('SECRET_EXPOSED' if leaked else 'SECRET_BLOCKED')
""".strip(),
    ),
    AttackProbe(
        name="container_escape",
        expected_marker="ESCAPE_BLOCKED",
        code="""
import ctypes
import os
from pathlib import Path

escaped = False
try:
    Path('/opt/sandbox/escape.txt').write_text('escape', encoding='utf-8')
except OSError:
    pass
else:
    escaped = True

try:
    os.setuid(0)
except OSError:
    pass
else:
    escaped = escaped or os.getuid() == 0

capability_line = next(
    line for line in Path('/proc/self/status').read_text().splitlines()
    if line.startswith('CapEff:')
)
escaped = escaped or int(capability_line.split()[1], 16) != 0

libc = ctypes.CDLL(None, use_errno=True)
CLONE_NEWNS = 0x00020000
if libc.unshare(CLONE_NEWNS) == 0:
    escaped = True

escaped = escaped or Path('/var/run/docker.sock').exists()
print('ESCAPE_SUCCEEDED' if escaped else 'ESCAPE_BLOCKED')
""".strip(),
    ),
    AttackProbe(
        name="fork_bomb",
        expected_marker="FORK_BLOCKED",
        code="""
import os

try:
    child_pid = os.fork()
except OSError:
    print('FORK_BLOCKED')
else:
    if child_pid == 0:
        os._exit(0)
    os.waitpid(child_pid, 0)
    print('FORK_UNEXPECTED')
""".strip(),
    ),
    AttackProbe(
        name="network",
        expected_marker="NETWORK_BLOCKED",
        code="""
import socket
from pathlib import Path

interfaces = {path.name for path in Path('/sys/class/net').iterdir()}
external_connected = False
for address in [('1.1.1.1', 53), ('8.8.8.8', 53)]:
    try:
        connection = socket.create_connection(address, timeout=0.5)
    except OSError:
        continue
    else:
        connection.close()
        external_connected = True

blocked = interfaces <= {'lo'} and not external_connected
print('NETWORK_BLOCKED' if blocked else 'NETWORK_AVAILABLE')
""".strip(),
    ),
)


async def run_checks() -> int:
    sandbox = DockerPythonSandbox()
    if not await sandbox.image_available():
        print("Sandbox image chưa tồn tại hoặc Docker Desktop chưa chạy.")
        return 1

    os.environ["SANDBOX_HOST_SECRET_CANARY"] = "day41-fake-secret-never-print"
    try:
        results = []
        for probe in PROBES:
            result = await sandbox.execute(PythonExecutionRequest(code=probe.code))
            passed = (
                result.status is PythonExecutionStatus.COMPLETED
                and result.stdout.strip() == probe.expected_marker
                and not result.artifacts
            )
            results.append(passed)
            print(
                f"{probe.name}: {'PASSED' if passed else 'FAILED'} (status={result.status.value})"
            )
    finally:
        os.environ.pop("SANDBOX_HOST_SECRET_CANARY", None)

    all_passed = all(results)
    print("SANDBOX_ATTACKS_PASSED" if all_passed else "SANDBOX_ATTACKS_FAILED")
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
