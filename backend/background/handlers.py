from collections.abc import Mapping
from typing import Any
from uuid import UUID

from backend.background.worker import BackgroundJobRegistry, JobExecutionContext
from backend.database import SessionFactory
from backend.services.audit_logs import record_audit_event
from backend.services.document_ingestion import prepare_document
from backend.services.documents import cleanup_deleted_document
from backend.services.memory import purge_expired_memories
from backend.services.operational_retention import purge_expired_operational_data


async def purge_expired_memories_handler(
    context: JobExecutionContext,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if payload:
        raise ValueError("maintenance.purge_expired_memories không nhận payload")
    await context.raise_if_cancelled()
    async with SessionFactory() as session:
        deleted = await purge_expired_memories(session)
    return {"deleted": deleted}


async def purge_operational_data_handler(
    context: JobExecutionContext,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if payload:
        raise ValueError("maintenance.purge_operational_data không nhận payload")
    await context.raise_if_cancelled()
    async with SessionFactory() as session:
        report = await purge_expired_operational_data(session)
        await record_audit_event(
            session,
            actor_user_id=None,
            action="maintenance.retention.run",
            target_type="operational_data",
            metadata=report.to_dict(),
        )
    return report.to_dict()


def build_background_job_registry() -> BackgroundJobRegistry:
    from backend.services.agent_jobs import agent_run_handler

    registry = BackgroundJobRegistry()
    registry.register("maintenance.purge_expired_memories", purge_expired_memories_handler)
    registry.register("maintenance.purge_operational_data", purge_operational_data_handler)
    registry.register("documents.cleanup", cleanup_document_handler)
    registry.register("documents.prepare", prepare_document_handler)
    registry.register("agent.run", agent_run_handler)
    return registry


async def cleanup_document_handler(
    context: JobExecutionContext, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if set(payload) != {"document_id"} or not isinstance(payload["document_id"], str):
        raise ValueError("Invalid document cleanup payload")
    return await cleanup_deleted_document(context, UUID(payload["document_id"]))


async def prepare_document_handler(
    context: JobExecutionContext, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if set(payload) != {"version_id"} or not isinstance(payload["version_id"], str):
        raise ValueError("Invalid document preparation payload")
    return await prepare_document(context, UUID(payload["version_id"]))
