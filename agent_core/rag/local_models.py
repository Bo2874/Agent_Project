"""Bounded, reusable subprocess IPC shared by local embedding and reranking models."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import psutil

MAX_FRAME_BYTES = 2 * 1024 * 1024


class LocalModelError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(message)


class LocalModelClient:
    module_name = ""
    error_type = LocalModelError
    error_prefix = "local_model"

    def __init__(
        self,
        *,
        project_root: Path,
        cache_path: Path,
        timeout_seconds: float = 600,
        memory_mb: int = 4096,
        batch_size: int = 4,
    ):
        self.project_root, self.cache_path = project_root, cache_path
        self.timeout_seconds, self.memory_mb, self.batch_size = (
            timeout_seconds,
            memory_mb,
            batch_size,
        )
        self._lock = asyncio.Lock()
        self._process = None
        self._revision = None
        self._profile = None
        self._users = 0

    async def close(self):
        async with self._lock:
            await self._stop()

    async def _stop(self):
        process, self._process = self._process, None
        self._revision, self._profile = None, None
        if process is None:
            return
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()

    async def _exchange(self, payload: dict) -> dict:
        process = self._process
        if process is None or process.returncode is not None:
            raise self.error_type(
                f"{self.error_prefix}_process", "Tiến trình model cục bộ đã dừng.", retryable=True
            )
        payload = {**payload, "request_id": uuid4().hex}
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if len(data) > MAX_FRAME_BYTES:
            raise self.error_type(
                f"{self.error_prefix}_input_limit", "Batch model cục bộ vượt giới hạn."
            )

        async def communicate():
            process.stdin.write(data)
            await process.stdin.drain()
            response = await process.stdout.readline()
            if not response or len(response) > MAX_FRAME_BYTES:
                raise self.error_type(
                    f"{self.error_prefix}_protocol", "Không nhận được kết quả model cục bộ hợp lệ."
                )
            try:
                result = json.loads(response)
                if result["request_id"] != payload["request_id"]:
                    raise ValueError("Response identity mismatch")
            except (ValueError, KeyError, TypeError) as exc:
                raise self.error_type(
                    f"{self.error_prefix}_protocol", "Phản hồi model cục bộ không hợp lệ."
                ) from exc
            if not result.get("ok"):
                raise self.error_type(
                    result["code"], result["message"], retryable=result.get("retryable", False)
                )
            return result

        task = asyncio.create_task(communicate())
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.5)
                if asyncio.get_running_loop().time() > deadline:
                    raise self.error_type(
                        f"{self.error_prefix}_timeout",
                        "Model cục bộ vượt giới hạn thời gian.",
                        retryable=True,
                    )
                try:
                    monitor = psutil.Process(process.pid)
                    rss = sum(
                        item.memory_info().rss
                        for item in [monitor, *monitor.children(recursive=True)]
                    )
                    if rss > self.memory_mb * 1024 * 1024:
                        raise self.error_type(
                            f"{self.error_prefix}_memory_limit",
                            "Model cục bộ vượt giới hạn bộ nhớ.",
                        )
                except psutil.NoSuchProcess:
                    pass
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _start(self, revision):
        if (
            self._process is not None
            and self._process.returncode is None
            and self._revision == revision
        ):
            return
        await self._stop()
        self.cache_path.mkdir(parents=True, exist_ok=True)
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "PATHEXT",
                "TEMP",
                "TMP",
                "LANG",
                "LC_ALL",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "REQUESTS_CA_BUNDLE",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
            }
        }
        env.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HOME": str(self.cache_path / "huggingface"),
                "TOKENIZERS_PARALLELISM": "false",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
                "OMP_NUM_THREADS": "2",
            }
        )
        kwargs = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                self.module_name,
                cwd=self.project_root,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_FRAME_BYTES,
                **kwargs,
            )
        )
        try:
            self._process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            self._process = await spawn
            await self._stop()
            raise
        except NotImplementedError:
            raise self.error_type(
                f"{self.error_prefix}_event_loop",
                "Event loop hiện tại không hỗ trợ tiến trình model. "
                "Trên Windows, chạy Uvicorn không có --reload và chỉ dùng một worker.",
            ) from None
        result = await self._exchange(
            {
                "operation": "load",
                "revision": revision,
                "parent_pid": os.getpid(),
                "parent_created_at": psutil.Process().create_time(),
            }
        )
        profile = result["profile"]
        self._validate_profile(profile, revision)
        self._revision, self._profile = revision, profile

    def _validate_profile(self, profile: dict, revision: str) -> None:
        raise NotImplementedError

    async def request(
        self,
        *,
        revision: str,
        payload: dict | None = None,
        on_load: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict:
        if self._users >= 8:
            raise self.error_type(
                f"{self.error_prefix}_busy", "Model đang bận. Hãy thử lại.", retryable=True
            )
        self._users += 1
        try:
            async with self._lock:
                try:
                    loading = not (
                        self._process is not None
                        and self._process.returncode is None
                        and self._revision == revision
                    )
                    if loading and on_load:
                        await on_load("running")
                    await self._start(revision)
                    if loading and on_load:
                        await on_load("completed")
                    result = await self._exchange(payload) if payload else {}
                    return {**result, "profile": dict(self._profile)}
                except asyncio.CancelledError:
                    await self._stop()
                    raise
                except LocalModelError:
                    await self._stop()
                    raise
                except Exception:
                    await self._stop()
                    raise self.error_type(
                        f"{self.error_prefix}_unavailable",
                        "Không thể chạy model cục bộ lúc này.",
                        retryable=True,
                    ) from None
        finally:
            self._users -= 1
