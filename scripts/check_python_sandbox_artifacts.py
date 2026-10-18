import asyncio
import hashlib
import sys

from agent_core.code_execution import (
    DockerPythonSandbox,
    PythonExecutionRequest,
    PythonExecutionStatus,
    SandboxUnavailableError,
)

ARTIFACT_TEST_CODE = """
import json
import os
import sys
from pathlib import Path

artifact_dir = Path(os.environ['AGENT_ARTIFACT_DIR'])
(artifact_dir / 'report.txt').write_text('sandbox report', encoding='utf-8')
(artifact_dir / 'data.json').write_text(
    json.dumps({'value': 6}, ensure_ascii=False), encoding='utf-8'
)
(artifact_dir / 'blocked.html').write_text('<script>bad()</script>', encoding='utf-8')
try:
    (artifact_dir / 'link.txt').symlink_to('/etc/passwd')
except OSError:
    pass
Path('/workspace/outside.txt').write_text('not an artifact', encoding='utf-8')
print('STDOUT_OK')
print('STDERR_OK', file=sys.stderr)
""".strip()


async def run_check() -> int:
    sandbox = DockerPythonSandbox()
    if not await sandbox.image_available():
        print("Sandbox image chưa tồn tại hoặc Docker Desktop chưa chạy.")
        return 1

    result = await sandbox.execute(PythonExecutionRequest(code=ARTIFACT_TEST_CODE))
    artifacts = {artifact.name: artifact for artifact in result.artifacts}
    expected_names = {"data.json", "report.txt"}
    checks = {
        "status": result.status is PythonExecutionStatus.COMPLETED,
        "stdout": result.stdout.strip() == "STDOUT_OK",
        "stderr": result.stderr.strip() == "STDERR_OK",
        "artifact_names": set(artifacts) == expected_names,
        "outside_not_returned": "outside.txt" not in artifacts,
        "unsafe_type_rejected": "artifact_rejected_media_type" in result.artifact_warnings,
        "symlink_rejected": "artifact_rejected_non_regular_file" in result.artifact_warnings,
    }
    for artifact in artifacts.values():
        checks[f"checksum_{artifact.name}"] = (
            artifact.sha256 == hashlib.sha256(artifact.content.encode()).hexdigest()
        )
        checks[f"size_{artifact.name}"] = artifact.size_bytes == len(artifact.content.encode())

    for name, passed in checks.items():
        print(f"{name}: {'PASSED' if passed else 'FAILED'}")
    all_passed = all(checks.values())
    print("SANDBOX_ARTIFACTS_PASSED" if all_passed else "SANDBOX_ARTIFACTS_FAILED")
    return 0 if all_passed else 1


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
