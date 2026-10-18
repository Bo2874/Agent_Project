"""Trusted container entrypoint. It is not imported by the web application."""

import hashlib
import json
import os
import re
import resource
import selectors
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

MAX_REQUEST_BYTES = 50_000
MAX_TIMEOUT_SECONDS = 30.0
MAX_CPU_SECONDS = 15
MAX_MEMORY_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_FILESYSTEM_BYTES = 100 * 1024 * 1024
MAX_PROCESSES = 8
MAX_OPEN_FILES = 64
SOURCE_PATH = Path("/workspace/main.py")
STDIN_PATH = Path("/workspace/stdin.txt")
ARTIFACT_DIRECTORY = Path("/workspace/artifacts")
ARTIFACT_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".txt": "text/plain",
}
ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    wall_timeout_seconds: float
    cpu_time_seconds: int
    memory_bytes: int
    max_output_bytes: int
    max_artifact_count: int
    max_artifact_bytes: int
    max_total_artifact_bytes: int
    max_filesystem_bytes: int
    max_processes: int
    max_open_files: int


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    status: str
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    output_truncated: bool = False


def emit(payload: dict[str, object]) -> None:
    sys.stdout.buffer.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    sys.stdout.buffer.flush()


def error_payload(message: str, *, duration_ms: int = 0) -> dict[str, object]:
    return {
        "status": "internal_error",
        "stdout": "",
        "stderr": message,
        "exit_code": None,
        "duration_ms": duration_ms,
        "output_truncated": False,
        "artifacts": [],
        "artifact_warnings": [],
    }


def require_positive_int(payload: dict[str, object], name: str, maximum: int) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ValueError(f"Sandbox limit {name} is invalid")
    return value


def parse_limits(payload: object, wall_timeout_seconds: float) -> ExecutionLimits:
    if not isinstance(payload, dict):
        raise ValueError("Sandbox limits must be an object")
    expected_fields = {
        "cpu_time_seconds",
        "memory_bytes",
        "max_output_bytes",
        "max_artifact_count",
        "max_artifact_bytes",
        "max_total_artifact_bytes",
        "max_filesystem_bytes",
        "max_processes",
        "max_open_files",
    }
    if set(payload) != expected_fields:
        raise ValueError("Sandbox limit fields are invalid")
    cpu_time_seconds = require_positive_int(payload, "cpu_time_seconds", MAX_CPU_SECONDS)
    if cpu_time_seconds > wall_timeout_seconds:
        raise ValueError("Sandbox CPU time cannot exceed wall time")
    limits = ExecutionLimits(
        wall_timeout_seconds=wall_timeout_seconds,
        cpu_time_seconds=cpu_time_seconds,
        memory_bytes=require_positive_int(payload, "memory_bytes", MAX_MEMORY_BYTES),
        max_output_bytes=require_positive_int(payload, "max_output_bytes", MAX_OUTPUT_BYTES),
        max_artifact_count=require_positive_int(payload, "max_artifact_count", 10),
        max_artifact_bytes=require_positive_int(payload, "max_artifact_bytes", 256 * 1024),
        max_total_artifact_bytes=require_positive_int(
            payload, "max_total_artifact_bytes", 512 * 1024
        ),
        max_filesystem_bytes=require_positive_int(
            payload, "max_filesystem_bytes", MAX_FILESYSTEM_BYTES
        ),
        max_processes=require_positive_int(payload, "max_processes", MAX_PROCESSES),
        max_open_files=require_positive_int(payload, "max_open_files", MAX_OPEN_FILES),
    )
    if limits.max_artifact_bytes > limits.max_total_artifact_bytes:
        raise ValueError("One artifact limit cannot exceed total artifact limit")
    return limits


def read_request() -> tuple[str, str, ExecutionLimits]:
    raw_request = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw_request) > MAX_REQUEST_BYTES:
        raise ValueError("Sandbox request is too large")
    payload = json.loads(raw_request.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Sandbox request must be an object")
    if set(payload) != {"code", "stdin", "wall_timeout_seconds", "limits"}:
        raise ValueError("Sandbox request fields are invalid")
    code = payload["code"]
    stdin = payload["stdin"]
    timeout = payload["wall_timeout_seconds"]
    if not isinstance(code, str) or not isinstance(stdin, str):
        raise ValueError("Sandbox code and stdin must be strings")
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise ValueError("Sandbox timeout must be numeric")
    timeout = float(timeout)
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError("Sandbox timeout is outside the allowed range")
    return code, stdin, parse_limits(payload["limits"], timeout)


def apply_process_limits(limits: ExecutionLimits) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_time_seconds, limits.cpu_time_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (limits.max_filesystem_bytes, limits.max_filesystem_bytes),
    )
    resource.setrlimit(
        resource.RLIMIT_NOFILE,
        (limits.max_open_files, limits.max_open_files),
    )


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def close_streams(process: subprocess.Popen[bytes]) -> None:
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()


def collect_bounded_output(
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
    max_output_bytes: int,
) -> ExecutionOutcome:
    output = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    assert process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    stored_bytes = 0

    try:
        while selector.get_map():
            remaining_seconds = deadline - monotonic()
            if remaining_seconds <= 0:
                stop_process(process)
                return ExecutionOutcome(
                    status="timed_out",
                    stdout=bytes(output["stdout"]),
                    stderr=bytes(output["stderr"]),
                    exit_code=None,
                )
            events = selector.select(timeout=min(remaining_seconds, 0.1))
            for key, _mask in events:
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available_bytes = max_output_bytes - stored_bytes
                accepted = chunk[:available_bytes]
                output[key.data].extend(accepted)
                stored_bytes += len(accepted)
                if len(accepted) < len(chunk):
                    stop_process(process)
                    return ExecutionOutcome(
                        status="resource_exhausted",
                        stdout=bytes(output["stdout"]),
                        stderr=bytes(output["stderr"]),
                        exit_code=None,
                        output_truncated=True,
                    )
    finally:
        selector.close()
        close_streams(process)

    return_code = process.wait()
    resource_signals = {-signal.SIGKILL, -signal.SIGXCPU, -signal.SIGXFSZ}
    if return_code in resource_signals:
        return ExecutionOutcome(
            status="resource_exhausted",
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
            exit_code=None,
        )
    return ExecutionOutcome(
        status="completed" if return_code == 0 else "failed",
        stdout=bytes(output["stdout"]),
        stderr=bytes(output["stderr"]),
        exit_code=return_code,
    )


def run_code(code: str, stdin: str, limits: ExecutionLimits, started_at: float) -> ExecutionOutcome:
    SOURCE_PATH.write_text(code, encoding="utf-8")
    STDIN_PATH.write_text(stdin, encoding="utf-8")
    ARTIFACT_DIRECTORY.mkdir(mode=0o700)
    safe_environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "AGENT_ARTIFACT_DIR": str(ARTIFACT_DIRECTORY),
    }
    with STDIN_PATH.open("rb") as stdin_file:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(SOURCE_PATH)],
            stdin=stdin_file,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=SOURCE_PATH.parent,
            env=safe_environment,
            start_new_session=True,
            preexec_fn=lambda: apply_process_limits(limits),
        )
        return collect_bounded_output(
            process,
            deadline=started_at + limits.wall_timeout_seconds,
            max_output_bytes=limits.max_output_bytes,
        )


def collect_artifacts(limits: ExecutionLimits) -> tuple[list[dict[str, object]], list[str]]:
    artifacts: list[dict[str, object]] = []
    warnings: list[str] = []
    total_bytes = 0
    try:
        with os.scandir(ARTIFACT_DIRECTORY) as entries:
            for scanned_count, directory_entry in enumerate(entries, start=1):
                if scanned_count > limits.max_artifact_count * 4:
                    warnings.append("artifact_rejected_scan_limit")
                    break
                if len(artifacts) >= limits.max_artifact_count:
                    warnings.append("artifact_rejected_count_limit")
                    break
                if not ARTIFACT_NAME_PATTERN.fullmatch(directory_entry.name):
                    warnings.append("artifact_rejected_unsafe_name")
                    continue
                entry = Path(directory_entry.path)
                suffix = entry.suffix.casefold()
                media_type = ARTIFACT_MEDIA_TYPES.get(suffix)
                if media_type is None:
                    warnings.append("artifact_rejected_media_type")
                    continue
                try:
                    if not stat.S_ISREG(directory_entry.stat(follow_symlinks=False).st_mode):
                        warnings.append("artifact_rejected_non_regular_file")
                        continue
                    descriptor = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(descriptor, "rb") as artifact_file:
                        raw_content = artifact_file.read(limits.max_artifact_bytes + 1)
                except OSError:
                    warnings.append("artifact_rejected_read_error")
                    continue
                if len(raw_content) > limits.max_artifact_bytes:
                    warnings.append("artifact_rejected_file_size")
                    continue
                if total_bytes + len(raw_content) > limits.max_total_artifact_bytes:
                    warnings.append("artifact_rejected_total_size")
                    continue
                try:
                    content = raw_content.decode("utf-8")
                    if "\x00" in content:
                        raise ValueError
                    if media_type == "application/json":
                        json.loads(content)
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    warnings.append("artifact_rejected_content")
                    continue
                artifacts.append(
                    {
                        "name": entry.name,
                        "media_type": media_type,
                        "size_bytes": len(raw_content),
                        "sha256": hashlib.sha256(raw_content).hexdigest(),
                        "content": content,
                    }
                )
                total_bytes += len(raw_content)
    except OSError:
        return artifacts, [*warnings, "artifact_directory_unavailable"]
    return artifacts, warnings


def main() -> None:
    started_at = monotonic()
    try:
        code, stdin, limits = read_request()
        outcome = run_code(code, stdin, limits, started_at)
        artifacts: list[dict[str, object]] = []
        artifact_warnings: list[str] = []
        if outcome.status == "completed":
            artifacts, artifact_warnings = collect_artifacts(limits)
        emit(
            {
                "status": outcome.status,
                "stdout": outcome.stdout.decode("utf-8", errors="ignore"),
                "stderr": outcome.stderr.decode("utf-8", errors="ignore"),
                "exit_code": outcome.exit_code,
                "duration_ms": round((monotonic() - started_at) * 1000),
                "output_truncated": outcome.output_truncated,
                "artifacts": artifacts,
                "artifact_warnings": artifact_warnings,
            }
        )
    except Exception:
        emit(
            error_payload(
                "Sandbox runner rejected or could not process the request.",
                duration_ms=round((monotonic() - started_at) * 1000),
            )
        )


if __name__ == "__main__":
    os.umask(0o077)
    main()
