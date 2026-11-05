import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from artifact_runtime.auth import signed_headers
from artifact_runtime.protocol import (
    MAX_MANIFEST_BYTES,
    MAX_TOTAL_BYTES,
    ArtifactInput,
    ArtifactRunRequest,
    GeneratedArtifact,
    bundle_header,
    canonical,
    unpack_bundle,
)
from artifact_runtime.temporary import temporary_workspace
from backend.services.artifacts.storage import ArtifactStorage, StoredArtifact


@dataclass(frozen=True)
class StagedArtifactResult:
    manifest: GeneratedArtifact
    files: tuple[StoredArtifact, ...]


class ArtifactRunnerClient:
    def __init__(
        self, url: str, *, key: str, storage: ArtifactStorage, owner: UUID, transport=None
    ):
        if len(key) < 32:
            raise ValueError("Runner signing key must contain at least 32 characters")
        self.url, self.key, self.storage, self.owner = url, key, storage, owner
        self.transport = transport

    async def execute(
        self,
        request: ArtifactRunRequest,
        inputs: tuple[ArtifactInput, ...],
        *,
        cancel: Callable[[], Awaitable[None]],
    ) -> StagedArtifactResult:
        if tuple(item.descriptor() for item in inputs) != request.inputs:
            raise ValueError("Input streams do not match the signed request")
        manifest = request.model_dump(mode="json")

        async def chunks():
            yield bundle_header(manifest)
            for item in inputs:
                while chunk := await asyncio.to_thread(item.source.read, 65536):
                    await cancel()
                    yield chunk

        async with httpx.AsyncClient(
            base_url=self.url, timeout=150, transport=self.transport, trust_env=False
        ) as client:

            async def perform():
                files = []
                try:
                    async with client.stream(
                        "POST",
                        "/v1/jobs",
                        content=chunks(),
                        headers=signed_headers(self.key, "POST", "/v1/jobs", canonical(manifest)),
                    ) as response:
                        if response.status_code != 200:
                            body = (await response.aread())[:8192]
                            try:
                                detail = json.loads(body).get(
                                    "detail", "Runner rejected the request"
                                )
                            except (ValueError, AttributeError):
                                detail = "Runner returned an invalid error response"
                            if not isinstance(detail, str):
                                detail = "Runner rejected the request"
                            detail = detail.splitlines()[-1][:1000] if detail else "Runner failed"
                            raise ValueError(f"Runner failed ({response.status_code}): {detail}")
                        with tempfile.TemporaryFile(dir=self.storage.root) as bundle:
                            size = 0
                            async for chunk in response.aiter_bytes(65536):
                                size += len(chunk)
                                if size > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + 4:
                                    raise ValueError("Runner output exceeds transport limit")
                                await asyncio.to_thread(bundle.write, chunk)
                            bundle.seek(0)
                            with temporary_workspace(
                                self.storage.root, "artifact-stage-"
                            ) as folder:
                                report, paths = await asyncio.to_thread(
                                    unpack_bundle, bundle, Path(folder)
                                )
                                for path in paths:
                                    with path.open("rb") as source:
                                        stored = await asyncio.to_thread(
                                            self.storage.put,
                                            source,
                                            f"{self.owner}/{uuid4()}/{uuid4()}/content.xlsx",
                                            max_bytes=20971520,
                                        )
                                    files.append(stored)
                    return StagedArtifactResult(report, tuple(files))
                except BaseException:
                    for stored in files:
                        await asyncio.to_thread(self.storage.delete, stored.key)
                    raise

            task = asyncio.create_task(perform())
            try:
                while not task.done():
                    await cancel()
                    await asyncio.wait({task}, timeout=0.2)
                return await task
            except BaseException:
                # Explicit cancellation is independent of the streaming response connection.
                try:
                    path = f"/v1/jobs/{request.job_id}"
                    await client.delete(path, headers=signed_headers(self.key, "DELETE", path, b""))
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                raise
