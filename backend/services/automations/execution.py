"""Trusted execution policies; request/model payloads cannot select a source."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from agent_core.graph import AgentLimits
from backend.background.queue import BackgroundJobOwnershipError
from backend.core.config import get_settings
from backend.database import SessionFactory
from backend.models import (
    AgentOperation,
    AgentRun,
    BackgroundJob,
    Conversation,
    GoogleFileGrant,
    GoogleMutation,
    GoogleWorkFile,
    Message,
    User,
)
from backend.models.automation import (
    ACTIVE_STATUSES,
    AutomationGrant,
    AutomationRevision,
    AutomationRun,
)
from backend.services.automations.service import owned_automation


def execution_limits_for_source(source, settings):
    if source not in {"chat", "automation"}:
        raise ValueError("Unknown execution source")
    if source == "automation":
        return AgentLimits(max_total_tokens=None)
    return AgentLimits(
        max_steps=settings.agent_max_steps,
        max_total_tokens=settings.agent_run_token_budget,
        max_duration_seconds=settings.agent_max_duration_seconds,
        max_identical_tool_calls=settings.agent_max_identical_tool_calls,
        max_repeated_observations=settings.agent_max_repeated_observations,
    )


async def admit_automation_run(session, context, identity):
    hint = await session.get(AutomationRun, identity)
    if hint is None:
        raise HTTPException(404, "Automation run not found")
    auto = await owned_automation(session, hint.owner_id, hint.automation_id, lock=True)
    row = await session.scalar(
        select(AutomationRun)
        .where(AutomationRun.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = await session.scalar(
        select(BackgroundJob)
        .where(BackgroundJob.id == context.job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        job is None
        or row.job_id != job.id
        or job.user_id != row.owner_id
        or job.status != "running"
        or job.worker_id != context.job.worker_id
        or job.attempt_count != context.job.attempt_count
        or job.started_at != context.job.started_at
        or job.lease_expires_at <= datetime.now(UTC)
    ):
        raise BackgroundJobOwnershipError("Automation job lease changed")
    if row.status not in {"queued", "running", "waiting_approval"}:
        return row
    owner = await session.get(User, row.owner_id)
    if (
        not get_settings().automations_enabled
        or owner is None
        or not owner.is_active
        or job.cancel_requested_at is not None
        or (row.trigger == "scheduled" and not auto.enabled and row.agent_run_id is None)
    ):
        row.status, row.stop_reason, row.finished_at = (
            "cancelled",
            "disabled_before_start",
            datetime.now(UTC),
        )
        await session.commit()
        return row
    if row.agent_run_id is None:
        from agent_core.prompts import build_chat_messages
        from agent_core.skills import SkillSelectionConfig
        from backend.routes.chat import chat_tool_selection_config
        from backend.services.agent_jobs import run_input
        from backend.services.google.access import grant_view
        from backend.services.run_events import fingerprint

        rev = await session.get(AutomationRevision, row.revision_id)
        conversation = Conversation(
            user_id=row.owner_id, title=auto.name[:160], source_kind="automation"
        )
        session.add(conversation)
        await session.flush()
        message = Message(conversation_id=conversation.id, role="user", content=rev.instruction)
        session.add(message)
        await session.flush()
        agent = AgentRun(
            conversation_id=conversation.id,
            user_message_id=message.id,
            status="queued",
            rag_trace={"durable": True, "source": "automation"},
        )
        session.add(agent)
        await session.flush()
        settings = get_settings()
        data = run_input(
            dict(
                conversation_id=conversation.id,
                user_message_id=message.id,
                user_id=row.owner_id,
                model_messages=build_chat_messages([message]),
                model=SimpleNamespace(
                    model=settings.deepseek_model, max_tokens=settings.deepseek_max_tokens
                ),
                user_message_content=rev.instruction,
                user_message_observed_at=message.created_at,
                limits=execution_limits_for_source("automation", settings),
                context_budget=settings.context_budget,
                tool_selection_config=chat_tool_selection_config(settings),
                skill_selection_config=SkillSelectionConfig(
                    minimum_score=settings.skill_selection_min_score,
                    ambiguity_margin=settings.skill_selection_ambiguity_margin,
                ),
                history_summary_context=(),
                rag_search_mode="fast",
            )
        )
        data["artifact_version_ids"] = list(rev.artifact_version_ids)
        refs = {}
        grants = (
            await session.scalars(
                select(AutomationGrant).where(AutomationGrant.revision_id == rev.id)
            )
        ).all()
        for grant in grants:
            if grant.file_id is None:
                continue
            selected = await session.scalar(
                select(GoogleFileGrant).where(
                    GoogleFileGrant.owner_id == row.owner_id,
                    GoogleFileGrant.connection_id == grant.connection_id,
                    GoogleFileGrant.file_id == grant.file_id,
                )
            )
            if (
                selected is None
                or selected.revoked
                or selected.generation != grant.grant_generation
                or selected.connection_generation != grant.connection_generation
            ):
                raise HTTPException(409, "Automation Google selection changed")
            key = selected.id
            refs[key] = {
                **grant_view(selected, conversation.id),
                "grant_generation": grant.grant_generation,
                "connection_generation": grant.connection_generation,
            }
            if not any(
                item.grant_id == selected.id
                for item in session.new
                if isinstance(item, GoogleWorkFile)
            ):
                session.add(
                    GoogleWorkFile(
                        context_id=conversation.id,
                        grant_id=selected.id,
                        owner_id=row.owner_id,
                        connection_id=selected.connection_id,
                        grant_generation=selected.generation,
                    )
                )
        data["google_file_refs"] = list(refs.values())
        session.add(
            AgentOperation(
                agent_run_id=agent.id,
                key="input",
                status="completed",
                fingerprint=fingerprint(data),
                result=data,
            )
        )
        row.agent_run_id, row.conversation_id, job.agent_run_id = (
            agent.id,
            conversation.id,
            agent.id,
        )
    row.status = "running"
    await session.commit()
    return row


async def sync_automation_run(session, run_id, *, drained=False, claimed=None):
    hint = await session.get(AutomationRun, run_id)
    if hint is None:
        return
    await owned_automation(session, hint.owner_id, hint.automation_id, lock=True)
    row = await session.scalar(
        select(AutomationRun)
        .where(AutomationRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row.status not in ACTIVE_STATUSES or row.agent_run_id is None:
        return row
    if claimed is not None:
        job = await session.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == row.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            job is None
            or job.id != claimed.id
            or job.worker_id != claimed.worker_id
            or job.attempt_count != claimed.attempt_count
            or job.started_at != claimed.started_at
            or job.status != "running"
            or job.lease_expires_at is None
            or job.lease_expires_at <= datetime.now(UTC)
        ):
            raise BackgroundJobOwnershipError("Automation projection claim changed")
    agent = await session.get(AgentRun, row.agent_run_id)
    unknown = await session.scalar(
        select(AgentOperation.id)
        .where(AgentOperation.agent_run_id == agent.id, AgentOperation.status == "started")
        .limit(1)
    )
    remote_unknown = await session.scalar(
        select(GoogleMutation.id)
        .where(GoogleMutation.run_id == agent.id, GoogleMutation.status.in_(["sent", "unknown"]))
        .limit(1)
    )
    unknown_reason = agent.stop_reason in {
        "partial_unknown_billing",
        "partial_unknown_tool_outcome",
        "agent_operation_unknown",
        "unknown_billing",
        "unknown_tool_outcome",
    }
    if unknown or remote_unknown or unknown_reason:
        row.status = "needs_reconciliation"
    elif agent.status == "waiting_approval" and row.status != "cancelling":
        row.status = "waiting_approval"
    elif not drained:
        if agent.status == "running" and row.status == "waiting_approval":
            row.status = "running"
    else:
        job = await session.get(BackgroundJob, row.job_id)
        if row.status == "cancelling" or (job and job.cancel_requested_at is not None):
            row.status = "cancelled"
        else:
            row.status = (
                "failed"
                if (agent.stop_reason or "").startswith("partial_")
                else {"completed": "succeeded", "cancelled": "cancelled"}.get(
                    agent.status, "failed"
                )
            )
        row.finished_at = datetime.now(UTC)
    row.stop_reason = agent.stop_reason
    from backend.services.agent_runs import list_agent_run_timelines

    timelines = await list_agent_run_timelines(session, row.conversation_id)
    timeline = next((item for item in timelines if item.id == agent.id), None)
    row.result = {
        **row.result,
        "total_tokens": timeline.total_tokens if timeline else 0,
        "usage_unknown": bool(unknown or remote_unknown or unknown_reason),
        "conversation_id": str(row.conversation_id),
        "agent_run_id": str(agent.id),
    }
    await session.commit()
    return row


async def start_automation_run(context, automation_run_id):
    from backend.services.agent_jobs import agent_run_handler

    try:
        async with SessionFactory() as session:
            row = await admit_automation_run(session, context, UUID(str(automation_run_id)))
            identity, agent_id = row.id, row.agent_run_id
            if row.status not in {"running", "waiting_approval"} or agent_id is None:
                return {"automation_run_id": str(identity), "status": row.status}
    except HTTPException:
        # Admission has no external side effects. Its failed transaction was
        # rolled back, so a known configuration error can release this slot.
        async with SessionFactory() as session:
            hint = await session.get(AutomationRun, automation_run_id)
            if hint is None or hint.job_id != context.job.id:
                raise
            await owned_automation(session, hint.owner_id, hint.automation_id, lock=True)
            row = await session.scalar(
                select(AutomationRun)
                .where(AutomationRun.id == hint.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            job = await session.scalar(
                select(BackgroundJob).where(BackgroundJob.id == context.job.id).with_for_update()
            )
            if (
                job is None
                or job.worker_id != context.job.worker_id
                or job.attempt_count != context.job.attempt_count
                or job.status != "running"
                or row.agent_run_id is not None
            ):
                raise BackgroundJobOwnershipError("Admission no longer belongs to worker") from None
            row.status, row.stop_reason, row.finished_at = (
                "failed",
                "configuration_changed",
                datetime.now(UTC),
            )
            row.result = {
                "message": "Selected input or configuration changed; review before running again."
            }
            await session.commit()
            return {"automation_run_id": str(row.id), "status": "failed"}
    try:
        await agent_run_handler(context, {"run_id": str(agent_id)})
    finally:

        async def reconcile():
            async with SessionFactory() as session:
                await sync_automation_run(session, identity, drained=True, claimed=context.job)

        await asyncio.shield(reconcile())
    return {"automation_run_id": str(identity)}


async def automation_run_handler(context, payload):
    if set(payload) != {"automation_run_id"}:
        raise ValueError("Invalid automation job payload")
    return await start_automation_run(context, UUID(payload["automation_run_id"]))
