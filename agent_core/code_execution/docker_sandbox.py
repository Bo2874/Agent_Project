import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from agent_core.code_execution.contract import (
    DEFAULT_PYTHON_EXECUTION_LIMITS,
    PythonExecutionArtifact,
    PythonExecutionLimits,
    PythonExecutionRequest,
    PythonExecutionResult,
    PythonExecutionStatus,
)

DEFAULT_SANDBOX_IMAGE = "personal-agent-python-sandbox:3.11-v1"
_IMAGE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:@-]{0,254}$")


class SandboxUnavailableError(RuntimeError):
    pass


class SandboxProtocolError(RuntimeError):
    pass


class SandboxResponseTooLargeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DockerSandboxConfig:
    image: str = DEFAULT_SANDBOX_IMAGE
    docker_executable: str = "docker"

    def __post_init__(self) -> None:
        if not _IMAGE_PATTERN.fullmatch(self.image):
            raise ValueError("Tên Docker image của sandbox không hợp lệ")
        if not self.docker_executable.strip():
            raise ValueError("Docker executable không được để trống")


class DockerPythonSandbox:
    """Execute one request in one disposable, network-isolated Docker container."""

    def __init__(
        self,
        config: DockerSandboxConfig | None = None,
        limits: PythonExecutionLimits = DEFAULT_PYTHON_EXECUTION_LIMITS,
    ) -> None:
        self.config = config or DockerSandboxConfig()
        self.limits = limits

    def build_run_command(self, container_name: str) -> tuple[str, ...]:
        workspace_bytes = self.limits.max_filesystem_bytes * 4 // 5
        temp_bytes = self.limits.max_filesystem_bytes - workspace_bytes
        workspace_options = (
            f"rw,noexec,nosuid,nodev,size={workspace_bytes},mode=700,uid=65532,gid=65532"
        )
        temp_options = f"rw,noexec,nosuid,nodev,size={temp_bytes},mode=700,uid=65532,gid=65532"
        return (
            self.config.docker_executable,
            "run",
            "--rm",
            "--interactive",
            "--name",
            container_name,
            "--network",
            "none",
            "--cpus",
            f"{self.limits.max_cpu_cores:g}",
            "--memory",
            str(self.limits.memory_bytes),
            "--memory-swap",
            str(self.limits.memory_bytes),
            "--pids-limit",
            str(self.limits.max_processes + 1),
            "--ulimit",
            f"cpu={self.limits.cpu_time_seconds}:{self.limits.cpu_time_seconds}",
            "--ulimit",
            f"nofile={self.limits.max_open_files}:{self.limits.max_open_files}",
            "--ulimit",
            f"fsize={self.limits.max_filesystem_bytes}:{self.limits.max_filesystem_bytes}",
            "--read-only",
            "--tmpfs",
            f"/workspace:{workspace_options}",
            "--tmpfs",
            f"/tmp:{temp_options}",
            "--workdir",
            "/workspace",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--security-opt",
            "seccomp=builtin",
            "--ipc",
            "none",
            self.config.image,
        )

    async def image_available(self) -> bool:
        try:
            return await asyncio.to_thread(self._image_available_sync)
        except (OSError, subprocess.SubprocessError):
            return False

    def _image_available_sync(self) -> bool:
        completed = subprocess.run(
            (
                self.config.docker_executable,
                "image",
                "inspect",
                self.config.image,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return completed.returncode == 0

    async def execute(self, request: PythonExecutionRequest) -> PythonExecutionResult:
        validated_request = PythonExecutionRequest.from_arguments(
            {"code": request.code, "stdin": request.stdin},
            limits=self.limits,
        )
        container_name = f"personal-agent-sandbox-{uuid4().hex}"
        command = self.build_run_command(container_name)
        payload = json.dumps(
            {
                "code": validated_request.code,
                "stdin": validated_request.stdin,
                "wall_timeout_seconds": self.limits.wall_timeout_seconds,
                "limits": {
                    "cpu_time_seconds": self.limits.cpu_time_seconds,
                    "memory_bytes": self.limits.memory_bytes,
                    "max_output_bytes": self.limits.max_output_bytes,
                    "max_artifact_count": self.limits.max_artifact_count,
                    "max_artifact_bytes": self.limits.max_artifact_bytes,
                    "max_total_artifact_bytes": self.limits.max_total_artifact_bytes,
                    "max_filesystem_bytes": self.limits.max_filesystem_bytes,
                    "max_processes": self.limits.max_processes,
                    "max_open_files": self.limits.max_open_files,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        started_at = monotonic()
        try:
            return_code, stdout = await asyncio.to_thread(
                self._run_container_sync,
                command,
                payload,
                container_name,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxUnavailableError("Docker CLI hoặc Docker engine không khả dụng") from exc
        except TimeoutError:
            await self._force_remove(container_name)
            return PythonExecutionResult(
                status=PythonExecutionStatus.TIMED_OUT,
                stderr="Sandbox container exceeded the host timeout.",
                duration_ms=round((monotonic() - started_at) * 1000),
            )
        except SandboxResponseTooLargeError:
            await self._force_remove(container_name)
            return PythonExecutionResult(
                status=PythonExecutionStatus.RESOURCE_EXHAUSTED,
                stderr="Sandbox response exceeded the host output limit.",
                duration_ms=round((monotonic() - started_at) * 1000),
                output_truncated=True,
            )
        except asyncio.CancelledError:
            await self._force_remove(container_name)
            raise

        if return_code == 137:
            return PythonExecutionResult(
                status=PythonExecutionStatus.RESOURCE_EXHAUSTED,
                stderr="Sandbox container exceeded its memory or process resource limit.",
                duration_ms=round((monotonic() - started_at) * 1000),
            )
        if return_code != 0:
            await self._force_remove(container_name)
            raise SandboxUnavailableError(
                "Docker sandbox không thể khởi động hoặc kết thúc bất thường"
            )
        return self._decode_result(stdout)

    def _run_container_sync(
        self,
        command: tuple[str, ...],
        payload: bytes,
        container_name: str,
    ) -> tuple[int, bytes]:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout, _docker_stderr = process.communicate(
                payload,
                timeout=self.limits.wall_timeout_seconds + 2,
            )
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            self._force_remove_sync(container_name)
            raise TimeoutError from exc

        untrusted_text_limit = self.limits.max_output_bytes + self.limits.max_total_artifact_bytes
        protocol_limit = untrusted_text_limit * 6 + 16_384
        if len(stdout) > protocol_limit:
            self._force_remove_sync(container_name)
            raise SandboxResponseTooLargeError
        return process.returncode, stdout

    def _decode_result(self, raw_output: bytes) -> PythonExecutionResult:
        try:
            payload = json.loads(raw_output.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError
            raw_artifacts = payload["artifacts"]
            raw_warnings = payload["artifact_warnings"]
            if not isinstance(raw_artifacts, list) or not isinstance(raw_warnings, list):
                raise TypeError
            if len(raw_warnings) > self.limits.max_artifact_count * 4 + 1:
                raise ValueError("Sandbox trả quá nhiều artifact warning")
            if any(
                not isinstance(warning, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", warning)
                for warning in raw_warnings
            ):
                raise ValueError("Sandbox artifact warning không hợp lệ")
            artifacts = tuple(
                PythonExecutionArtifact.from_payload(artifact) for artifact in raw_artifacts
            )
            artifact_warnings = tuple(raw_warnings)
            result = PythonExecutionResult(
                status=PythonExecutionStatus(payload["status"]),
                stdout=payload["stdout"],
                stderr=payload["stderr"],
                exit_code=payload["exit_code"],
                duration_ms=payload["duration_ms"],
                output_truncated=payload["output_truncated"],
                artifacts=artifacts,
                artifact_warnings=artifact_warnings,
            )
            output_bytes = len(result.stdout.encode()) + len(result.stderr.encode())
            if output_bytes > self.limits.max_output_bytes:
                raise ValueError("Sandbox output vượt giới hạn")
            if len(result.artifacts) > self.limits.max_artifact_count:
                raise ValueError("Sandbox trả quá nhiều artifact")
            if any(
                artifact.size_bytes > self.limits.max_artifact_bytes
                for artifact in result.artifacts
            ):
                raise ValueError("Sandbox artifact vượt giới hạn file")
            if (
                sum(artifact.size_bytes for artifact in result.artifacts)
                > self.limits.max_total_artifact_bytes
            ):
                raise ValueError("Sandbox artifact vượt giới hạn tổng")
            return result
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            raise SandboxProtocolError("Sandbox trả về response không hợp lệ") from exc

    async def _force_remove(self, container_name: str) -> None:
        await asyncio.to_thread(self._force_remove_sync, container_name)

    def _force_remove_sync(self, container_name: str) -> None:
        try:
            subprocess.run(
                (
                    self.config.docker_executable,
                    "rm",
                    "--force",
                    container_name,
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
