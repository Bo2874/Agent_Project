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
from backend.services.run_event_notifications import notify_run_event


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


async def sync_waiting_automation_approval(approval_id):
    """Project only after both sides of the durable approval handshake exist."""
    from backend.models import AgentApproval, RunStep, ToolCall
    from backend.services.run_events import current_job

    async with SessionFactory() as session:
        identity = await session.scalar(
            select(AutomationRun.id)
            .join(AgentRun, AgentRun.id == AutomationRun.agent_run_id)
            .join(AgentApproval, AgentApproval.agent_run_id == AgentRun.id)
            .join(RunStep, RunStep.agent_run_id == AgentRun.id)
            .join(ToolCall, ToolCall.run_step_id == RunStep.id)
            .where(
                AgentApproval.id == UUID(str(approval_id)),
                AgentApproval.decision.is_(None),
                ToolCall.approval_id == AgentApproval.id,
                ToolCall.status == "waiting_approval",
                ToolCall.approval_decision.is_(None),
                AgentRun.status == "waiting_approval",
                AutomationRun.status.in_(["running", "waiting_approval"]),
            )
            .limit(1)
        )
        if identity:
            await sync_automation_run(session, identity, claimed=current_job.get())


async def reconcile_terminal_automation_jobs(session, *, queue_name, limit=100):
    """Repair projections after a dead worker, including a crash after queue commit.

    Called after queue lease recovery releases job locks, and before scheduler dispatch.
    Scan durable terminal jobs every tick so interruption of this repair is harmless.
    """
    from backend.models import RunEvent
    from backend.models.automation import Automation
    from backend.services.automations.notifications import queue_result_notification

    recoverable = ("queued", "running", "waiting_approval", "cancelling")
    hints = (
        await session.execute(
            select(AutomationRun.id, AutomationRun.automation_id)
            .join(BackgroundJob, BackgroundJob.id == AutomationRun.job_id)
            .where(
                AutomationRun.status.in_(recoverable),
                BackgroundJob.status.in_(["failed", "cancelled", "succeeded"]),
                BackgroundJob.queue_name == queue_name,
            )
            .order_by(AutomationRun.automation_id, AutomationRun.id)
            .limit(limit)
        )
    ).all()
    count = 0
    for identity, auto_id in hints:
        auto = await session.scalar(
            select(Automation)
            .where(Automation.id == auto_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if auto is None:
            continue
        row = await session.scalar(
            select(AutomationRun)
            .where(AutomationRun.id == identity)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        job = await session.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == row.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row.status not in recoverable or job.status not in {"failed", "cancelled", "succeeded"}:
            await session.commit()
            continue
        now = datetime.now(UTC)
        reason = "user_cancelled" if job.status == "cancelled" else job.error_code or "job_ended"
        if row.agent_run_id:
            agent = await session.get(AgentRun, row.agent_run_id, populate_existing=True)
            if agent.status in {"queued", "running", "waiting_approval"}:
                agent.status = "cancelled" if job.status == "cancelled" else "failed"
                agent.stop_reason, agent.finished_at = reason, now
                agent.event_sequence += 1
                session.add(
                    RunEvent(
                        agent_run_id=agent.id,
                        sequence=agent.event_sequence,
                        kind="cancelled" if agent.status == "cancelled" else "error",
                        data={
                            "message": "Automation worker ended; state recovered.",
                            "resumable": False,
                        },
                    )
                )
                await notify_run_event(session, agent.id)
            # This retains completed outputs, and keeps uncertain operations active.
            await sync_automation_run(session, row.id, drained=True)
        else:
            row.status = "cancelled" if job.status == "cancelled" else "failed"
            row.stop_reason, row.finished_at = reason, now
            await queue_result_notification(session, row.id)
            await session.commit()
        count += 1
    return count


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
        from backend.services.automations.notifications import queue_result_notification

        await queue_result_notification(session, row.id)
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
    row.stop_reason = "user_cancelled" if row.status == "cancelled" else agent.stop_reason
    from backend.services.agent_runs import list_agent_run_timelines

    timelines = await list_agent_run_timelines(session, row.conversation_id)
    timeline = next((item for item in timelines if item.id == agent.id), None)
    completed = (
        await session.scalars(
            select(GoogleMutation)
            .where(
                GoogleMutation.run_id == agent.id,
                GoogleMutation.owner_id == row.owner_id,
                GoogleMutation.context_id == row.conversation_id,
                GoogleMutation.status == "completed",
            )
            .order_by(GoogleMutation.id)
            .limit(100)
        )
    ).all()
    row.result = {
        **row.result,
        "total_tokens": timeline.total_tokens if timeline else 0,
        "usage_unknown": bool(unknown or remote_unknown or unknown_reason),
        "conversation_id": str(row.conversation_id),
        "agent_run_id": str(agent.id),
        "google_results": [
            {
                key: value
                for key, value in (operation.receipt or {}).items()
                if key in {"operation_id", "url", "updated_range", "updated_rows", "updated_cells"}
            }
            for operation in completed
        ],
    }
    uncertain = (
        await session.scalars(
            select(GoogleMutation)
            .where(
                GoogleMutation.run_id == agent.id,
                GoogleMutation.owner_id == row.owner_id,
                GoogleMutation.context_id == row.conversation_id,
                GoogleMutation.status.in_(["sent", "unknown"]),
            )
            .order_by(GoogleMutation.id)
            .limit(100)
        )
    ).all()
    from backend.schemas.google import DOC_MIME, SHEET_MIME
    from backend.services.google.access import google_url

    row.result = {
        **row.result,
        "uncertain_actions": [
            {
                "operation_id": str(operation.id),
                "operation": operation.kind,
                "url": google_url(
                    operation.file_id,
                    SHEET_MIME
                    if operation.kind
                    in {"append_rows", "update_range", "set_formulas", "format_range"}
                    else DOC_MIME,
                )
                if operation.file_id
                else None,
            }
            for operation in uncertain
        ],
    }
    from backend.models import ArtifactVersion
    from backend.services.artifacts.service import published

    versions = (
        await session.scalars(
            select(ArtifactVersion)
            .where(
                ArtifactVersion.source_run_id == agent.id,
                ArtifactVersion.owner_user_id == row.owner_id,
            )
            .order_by(ArtifactVersion.created_at, ArtifactVersion.id)
            .limit(100)
        )
    ).all()
    row.result = {
        **row.result,
        "artifact_results": [published(version).model_dump(mode="json") for version in versions],
    }
    from backend.services.automations.notifications import queue_result_notification

    await queue_result_notification(session, row.id)
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
            from backend.services.automations.notifications import queue_result_notification

            await queue_result_notification(session, row.id)
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
