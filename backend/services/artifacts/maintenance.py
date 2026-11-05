"""Conservative artifact reconciliation; never replay unknown external actions."""

import asyncio
import hashlib
import os
import time
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select

from agent_core.tools import ToolResult
from backend.models import AgentOperation, RunStep, ToolCall, User
from backend.models.artifact import ArtifactVersion
from backend.services.artifacts.publication import OUTPUT_TOOLS
from backend.services.artifacts.service import published


async def recover_artifact_operations(session, owner, run_id):
    from agent_core.graph.runtime import _fingerprint as fingerprint

    operations = (
        await session.scalars(
            select(AgentOperation)
            .where(AgentOperation.agent_run_id == run_id, AgentOperation.status == "started")
            .with_for_update()
        )
    ).all()
    recovered = 0
    for operation in operations:
        if ".tool." not in operation.key:
            continue
        step, separator, call_id = operation.key.rsplit(".tool.", 1)[1].partition(".")
        if not separator or not step.isdigit():
            continue
        call = await session.scalar(
            select(ToolCall)
            .join(RunStep, RunStep.id == ToolCall.run_step_id)
            .where(
                RunStep.agent_run_id == run_id,
                RunStep.step_number == int(step),
                ToolCall.provider_call_id == call_id,
            )
            .order_by(RunStep.attempt_number.desc(), ToolCall.created_at.desc())
            .limit(1)
        )
        if (
            call is None
            or call.tool_name not in OUTPUT_TOOLS
            or fingerprint({"name": call.tool_name, "arguments": call.arguments})
            != operation.fingerprint
            or (call.tool_name == "run_artifact_python" and call.approval_decision != "approved")
        ):
            continue
        key = "artifact:" + hashlib.sha256(f"{run_id}:{operation.key}".encode()).hexdigest()
        version = await session.scalar(
            select(ArtifactVersion).where(
                ArtifactVersion.owner_user_id == owner,
                ArtifactVersion.source_run_id == run_id,
                ArtifactVersion.operation_key == key,
            )
        )
        if version:
            operation.status = "completed"
            operation.actual_tokens = 0
            operation.result = asdict(
                ToolResult.success(
                    "Phiên bản đã được lưu trước gián đoạn.",
                    data={"artifact_refs": [published(version).model_dump(mode="json")]},
                )
            )
        else:
            # The exact saved call can only retry its deterministic runner IDs. Broker tombstones
            # refuse jobs interrupted before completion; completed bundles are replayed, not rerun.
            await session.delete(operation)
        recovered += 1
    await session.flush()
    return recovered


async def reconcile_orphans(sessions, storage, *, grace_seconds=86400, limit=1000, dry_run=False):
    """Delete only old, generated, unreferenced files while holding their owner's publish lock.

    Default grace exceeds every runner/tool timeout. A database failure stops deletion.
    Follow neither symlink directories nor invalid storage keys; leave unknown files alone.
    """
    if grace_seconds < 3600 or limit < 1:
        raise ValueError("Orphan cleanup requires at least one hour of grace and a positive limit")
    cutoff, removed = time.time() - grace_seconds, 0
    for folder, dirs, files in os.walk(storage.root, followlinks=False):
        from pathlib import Path

        dirs[:] = [name for name in dirs if not (Path(folder) / name).is_symlink()]
        for filename in files:
            path = Path(folder) / filename
            key = path.relative_to(storage.root).as_posix()
            try:
                owner = UUID(key.split("/", 1)[0])
                target = storage.path(key)
                before = target.stat()
            except (ValueError, OSError):
                continue
            if before.st_mtime >= cutoff:
                continue
            async with sessions() as session:
                await session.scalar(select(User).where(User.id == owner).with_for_update())
                referenced = await session.scalar(
                    select(ArtifactVersion.id).where(ArtifactVersion.storage_key == key)
                )
                after = target.stat()
                if referenced is None and (before.st_ino, before.st_mtime_ns, before.st_size) == (
                    after.st_ino,
                    after.st_mtime_ns,
                    after.st_size,
                ):
                    if not dry_run:
                        storage.delete(key)
                    removed += 1
            if removed >= limit:
                return removed
    # Scratch has its own generated namespace and an OS-held active-workspace lease.
    from artifact_runtime.temporary import reap_workspaces

    async with sessions() as session:
        await session.scalar(select(1))
    removed += await asyncio.to_thread(
        reap_workspaces,
        storage.root,
        "artifact-stage-",
        grace_seconds=grace_seconds,
        limit=limit - removed,
        dry_run=dry_run,
    )
    return removed
