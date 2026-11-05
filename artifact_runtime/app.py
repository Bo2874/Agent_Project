"""Private, authenticated runner HTTP API. Never load backend settings or secrets here."""

import asyncio
import hashlib
import hmac
import logging
import os
import re
import struct
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from artifact_runtime.auth import sign
from artifact_runtime.containers import DockerBroker, RunnerError
from artifact_runtime.protocol import MAX_MANIFEST_BYTES, ArtifactRunRequest
from artifact_runtime.temporary import temporary_workspace

logger = logging.getLogger(__name__)


class RequestReader:
    def __init__(self, request: Request):
        self.iterator = request.stream().__aiter__()
        self.buffer = b""
        self.deadline = asyncio.get_running_loop().time() + 30

    async def take(self, count: int) -> bytes:
        parts = []
        while count:
            if not self.buffer:
                try:
                    async with asyncio.timeout_at(self.deadline):
                        self.buffer = await anext(self.iterator)
                except StopAsyncIteration:
                    raise ValueError("Truncated runner request") from None
                if not self.buffer:
                    continue
            part, self.buffer = self.buffer[:count], self.buffer[count:]
            parts.append(part)
            count -= len(part)
        return b"".join(parts)

    async def end(self):
        if self.buffer:
            raise ValueError("Unexpected trailing request content")
        async with asyncio.timeout_at(self.deadline):
            async for chunk in self.iterator:
                if chunk:
                    raise ValueError("Unexpected trailing request content")


def create_app(broker: DockerBroker, *, key: str) -> FastAPI:
    if len(key) < 32:
        raise ValueError("Runner signing key must contain at least 32 characters")
    active: dict[UUID, asyncio.Task] = {}

    async def sweep():
        while True:
            await asyncio.sleep(30)
            try:
                await broker.reap_stale()
            except Exception:
                # Cancellation remains a BaseException; sweep failures are visible
                # in logs and cannot disable cleanup for the process lifetime.
                logger.exception("Artifact reaper failed; retrying on the next sweep")

    @asynccontextmanager
    async def lifespan(_app):
        await broker.recover()
        task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            task.cancel()
            for running in tuple(active.values()):
                running.cancel()
            await asyncio.gather(task, *active.values(), return_exceptions=True)

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(request: Request, manifest: bytes):
        try:
            expires = int(request.headers.get("x-artifact-expires", "0"))
            nonce = request.headers.get("x-artifact-nonce", "")
            provided = request.headers.get("x-artifact-signature", "")
            if not time.time() <= expires <= time.time() + 300 or not re.fullmatch(
                "[0-9a-f]{32}", nonce
            ):
                raise ValueError("Expired or invalid runner authentication")
            expected = sign(
                key, request.method, request.url.path, manifest, expires=expires, nonce=nonce
            )
            if not hmac.compare_digest(provided, expected):
                raise ValueError("Invalid runner signature")
            broker.accept_nonce(nonce, expires)
        except (ValueError, RunnerError) as error:
            raise HTTPException(401, "Runner authentication failed") from error

    @application.get("/health/live")
    async def health():
        return {"status": "ok"}

    @application.delete("/v1/jobs/{job_id}")
    async def cancel_job(job_id: UUID, request: Request):
        authenticate(request, b"")
        broker.mark_cancelled(job_id)
        task = active.get(job_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await broker.cleanup(job_id)
        return {"status": "cancelled"}

    @application.post("/v1/jobs")
    async def run_job(request: Request):
        async with asyncio.timeout(140):
            reader = RequestReader(request)
            try:
                size = struct.unpack("!I", await reader.take(4))[0]
                if not 0 < size <= MAX_MANIFEST_BYTES:
                    raise ValueError("Invalid runner manifest length")
                manifest = await reader.take(size)
                authenticate(request, manifest)
                operation = ArtifactRunRequest.model_validate_json(manifest)
                if len(active) >= 2 and operation.job_id not in active:
                    raise HTTPException(429, "Runner is busy; retry later")
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            with temporary_workspace(broker.root, "artifact-upload-") as folder:
                paths = []
                try:
                    for item in operation.inputs:
                        path = Path(folder) / item.name
                        digest = hashlib.sha256()
                        with path.open("xb") as output:
                            remaining = item.size_bytes
                            while remaining:
                                chunk = await reader.take(min(65536, remaining))
                                digest.update(chunk)
                                output.write(chunk)
                                remaining -= len(chunk)
                        if digest.hexdigest() != item.sha256:
                            raise ValueError("Runner input checksum mismatch")
                        paths.append(path)
                    await reader.end()
                except ValueError as error:
                    raise HTTPException(422, str(error)) from error

                # Open streams belong to the task until the fixed runtime has consumed them.
                async def perform():
                    streams = tuple(path.open("rb") for path in paths)
                    try:
                        return await broker.run(operation, streams)
                    finally:
                        for source in streams:
                            source.close()

                # Never replace a live task for the same job id, including cancellation routing.
                if operation.job_id in active:
                    raise HTTPException(409, "Runner job is already active")
                if len(active) >= 2:
                    raise HTTPException(429, "Runner is busy; retry later")
                task = asyncio.create_task(perform())
                active[operation.job_id] = task
                try:
                    # The reader's upload deadline is independent of the runtime profile.
                    result = await task
                except asyncio.CancelledError:
                    raise HTTPException(409, "Runner job was cancelled") from None
                except (RunnerError, ValueError, TimeoutError) as error:
                    raise HTTPException(422, str(error)[-4096:]) from error
                finally:
                    active.pop(operation.job_id, None)
        return FileResponse(result, media_type="application/vnd.personal-agent.artifact-bundle")

    return application


def application_factory() -> FastAPI:
    return create_app(
        DockerBroker(
            Path(os.environ.get("ARTIFACT_RUNNER_STATE_PATH", "/var/lib/artifact-runner")),
            docker=os.environ.get("ARTIFACT_DOCKER", "docker"),
        ),
        key=os.environ.get("ARTIFACT_RUNNER_KEY", ""),
    )
