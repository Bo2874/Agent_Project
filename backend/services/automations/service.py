"""Owner-scoped configuration and immutable execution snapshots."""

import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from backend.models import ArtifactVersion, BackgroundJob, GoogleConnection, GoogleFileGrant
from backend.models.automation import (
    ACTIVE_STATUSES,
    Automation,
    AutomationGrant,
    AutomationRevision,
    AutomationRun,
)
from backend.schemas.automation import AutomationRunView, AutomationView, GrantDraft


def audit(session, owner, action, identity, **metadata):
    from backend.models import AuditLog

    session.add(
        AuditLog(
            actor_user_id=owner,
            action="automation." + action,
            target_type="automation",
            target_id=str(identity),
            outcome="success",
            metadata_json=metadata,
        )
    )


async def owned_automation(session, owner, identity, *, lock=False):
    statement = (
        select(Automation)
        .where(Automation.id == identity, Automation.owner_id == owner)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise HTTPException(404, "Automation not found")
    return row


async def current_revision(session, auto):
    return await session.scalar(
        select(AutomationRevision).where(
            AutomationRevision.automation_id == auto.id,
            AutomationRevision.owner_id == auto.owner_id,
            AutomationRevision.number == auto.revision_number,
        )
    )


async def view(session, auto):
    rev = await current_revision(session, auto)
    grants = (
        await session.scalars(
            select(AutomationGrant)
            .where(AutomationGrant.revision_id == rev.id)
            .order_by(AutomationGrant.id)
        )
    ).all()
    from backend.services.artifacts.service import published

    versions = (
        (
            await session.scalars(
                select(ArtifactVersion)
                .where(
                    ArtifactVersion.owner_user_id == auto.owner_id,
                    ArtifactVersion.id.in_([UUID(value) for value in rev.artifact_version_ids]),
                )
                .order_by(ArtifactVersion.id)
            )
        ).all()
        if rev.artifact_version_ids
        else []
    )
    return AutomationView(
        id=auto.id,
        name=auto.name,
        enabled=auto.enabled,
        revision_id=rev.id,
        revision_number=rev.number,
        instruction=rev.instruction,
        schedule=rev.schedule,
        grants=[GrantDraft.model_validate(g) for g in grants],
        artifact_version_ids=rev.artifact_version_ids,
        artifact_actions=rev.artifact_actions,
        artifact_inputs=[
            {**published(version).model_dump(mode="json"), "version_number": version.version_number}
            for version in versions
        ],
        next_run_at=auto.next_run_at,
    )


async def validate_draft(session, owner, draft):
    pinned = []
    for proposed in draft.grants:
        conn = await session.scalar(
            select(GoogleConnection).where(
                GoogleConnection.id == proposed.connection_id, GoogleConnection.owner_id == owner
            )
        )
        if conn is None or conn.status != "connected" or not conn.offline_ready:
            raise HTTPException(404, "Offline-ready selected Google connection not found")
        generation = None
        if proposed.file_id is not None:
            grant = await session.scalar(
                select(GoogleFileGrant).where(
                    GoogleFileGrant.owner_id == owner,
                    GoogleFileGrant.connection_id == conn.id,
                    GoogleFileGrant.file_id == proposed.file_id,
                    GoogleFileGrant.revoked.is_(False),
                    GoogleFileGrant.connection_generation == conn.generation,
                )
            )
            if grant is None:
                raise HTTPException(404, "Selected Google file not found")
            if proposed.action not in {"read_sheet", "read_doc"} and not grant.can_edit:
                raise HTTPException(409, "Google file is read-only")
            from backend.schemas.google import DOC_MIME, SHEET_MIME

            expected = DOC_MIME if proposed.action in {"read_doc", "edit_doc"} else SHEET_MIME
            if grant.mime_type != expected:
                raise HTTPException(422, "Action does not match Google file type")
            generation = grant.generation
        pinned.append(
            {
                **proposed.model_dump(),
                "connection_generation": conn.generation,
                "grant_generation": generation,
            }
        )
    for identity in draft.artifact_version_ids:
        if (
            await session.scalar(
                select(ArtifactVersion.id).where(
                    ArtifactVersion.id == identity, ArtifactVersion.owner_user_id == owner
                )
            )
            is None
        ):
            raise HTTPException(404, "Selected artifact version not found")
    return pinned


async def save_revision(session, auto, draft, pinned):
    rev = AutomationRevision(
        automation_id=auto.id,
        owner_id=auto.owner_id,
        number=auto.revision_number,
        instruction=draft.instruction,
        schedule=draft.schedule.model_dump(mode="json"),
        artifact_version_ids=[str(v) for v in draft.artifact_version_ids],
        artifact_actions=draft.artifact_actions,
    )
    session.add(rev)
    await session.flush()
    session.add_all(
        [
            AutomationGrant(
                revision_id=rev.id, automation_id=auto.id, owner_id=auto.owner_id, **pin
            )
            for pin in pinned
        ]
    )
    await session.flush()
    return rev


async def create_automation(session, owner: UUID, draft):
    pinned = await validate_draft(session, owner, draft)
    auto = Automation(owner_id=owner, name=draft.name, enabled=False)
    session.add(auto)
    await session.flush()
    rev = await save_revision(session, auto, draft, pinned)
    audit(session, owner, "create", auto.id, revision=str(rev.id))
    await session.commit()
    return await view(session, auto)


async def revise_automation(session, owner, automation_id, draft):
    auto = await owned_automation(session, owner, automation_id, lock=True)
    pinned = await validate_draft(session, owner, draft)
    auto.name = draft.name
    auto.revision_number += 1
    rev = await save_revision(session, auto, draft, pinned)
    if auto.enabled:
        from datetime import UTC, datetime

        from backend.services.automations.schedules import next_occurrence

        auto.next_run_at = next_occurrence(draft.schedule, after=datetime.now(UTC))
    audit(session, owner, "revise", auto.id, revision=str(rev.id))
    await session.commit()
    return await view(session, auto)


async def get_owned_automation_run(session, owner, run_id):
    row = await session.scalar(
        select(AutomationRun).where(AutomationRun.id == run_id, AutomationRun.owner_id == owner)
    )
    if row is None:
        raise HTTPException(404, "Automation run not found")
    rev = await session.get(AutomationRevision, row.revision_id)
    approvals = []
    if row.status == "waiting_approval" and row.agent_run_id:
        from backend.models import AgentApproval, RunStep, ToolCall

        pending = (
            await session.execute(
                select(AgentApproval, ToolCall)
                .join(ToolCall, ToolCall.approval_id == AgentApproval.id)
                .join(RunStep, RunStep.id == ToolCall.run_step_id)
                .where(
                    AgentApproval.agent_run_id == row.agent_run_id,
                    RunStep.agent_run_id == row.agent_run_id,
                    AgentApproval.decision.is_(None),
                    AgentApproval.expires_at > utcnow(),
                    ToolCall.status == "waiting_approval",
                    ToolCall.approval_decision.is_(None),
                )
                .limit(20)
            )
        ).all()
        approvals = [
            {
                "id": str(approval.id),
                "tool_name": call.tool_name,
                "arguments": call.arguments,
                "expires_at": approval.expires_at.isoformat(),
            }
            for approval, call in pending
        ]
    return AutomationRunView.model_validate(row).model_copy(
        update={
            "revision_number": rev.number,
            "instruction": rev.instruction,
            "pending_approvals": approvals,
        }
    )


def utcnow():
    return datetime.now(UTC)


async def lock_run(session, owner, run_id):
    hint = await session.scalar(
        select(AutomationRun).where(AutomationRun.id == run_id, AutomationRun.owner_id == owner)
    )
    if hint is None:
        raise HTTPException(404, "Automation run not found")
    auto = await owned_automation(session, owner, hint.automation_id, lock=True)
    row = await session.scalar(
        select(AutomationRun)
        .where(AutomationRun.id == run_id, AutomationRun.owner_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return auto, row


async def stop_locked(session, row):
    if row.status not in ACTIVE_STATUSES:
        return
    job = await session.scalar(
        select(BackgroundJob)
        .where(BackgroundJob.id == row.job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = utcnow()
    if job and job.status in {"queued", "running"}:
        job.cancel_requested_at = job.cancel_requested_at or now
        if job.status == "queued":
            job.status, job.cancelled_at, job.finished_at = "cancelled", now, now
    if row.status == "queued" and row.agent_run_id is None:
        row.status, row.stop_reason, row.finished_at = "cancelled", "user_cancelled", now
        from backend.services.automations.notifications import queue_result_notification

        await queue_result_notification(session, row.id)
    elif row.status != "needs_reconciliation":
        row.status = "cancelling"


async def request_run_stop(session, owner, run_id):
    _, row = await lock_run(session, owner, run_id)
    await stop_locked(session, row)
    audit(session, owner, "stop_requested", row.id)
    await session.commit()
    job = await session.get(BackgroundJob, row.job_id) if row.job_id else None
    if row.status == "cancelling" and (
        job is None or job.status in {"failed", "cancelled", "succeeded"}
    ):
        from backend.services.automations.execution import sync_automation_run

        await sync_automation_run(session, row.id, drained=True)
    return AutomationRunView.model_validate(row)


async def set_schedule_enabled(session, owner, automation_id, *, enabled, stop_active=False):
    auto = await owned_automation(session, owner, automation_id, lock=True)
    if enabled and not auto.enabled:
        from backend.schemas.automation import ScheduleSpec
        from backend.services.automations.schedules import next_occurrence

        rev = await current_revision(session, auto)
        auto.next_run_at = next_occurrence(
            ScheduleSpec.model_validate(rev.schedule), after=utcnow()
        )
    elif not enabled:
        auto.next_run_at = None
        runs = (
            await session.scalars(
                select(AutomationRun)
                .where(
                    AutomationRun.automation_id == auto.id,
                    AutomationRun.owner_id == owner,
                    AutomationRun.status.in_(ACTIVE_STATUSES),
                )
                .order_by(AutomationRun.id)
                .with_for_update()
            )
        ).all()
        for row in runs:
            if stop_active or (row.trigger == "scheduled" and row.status == "queued"):
                await stop_locked(session, row)
    auto.enabled = enabled
    audit(session, owner, "schedule_changed", auto.id, enabled=enabled, stop_active=stop_active)
    await session.commit()
    return await view(session, auto)


async def run_now(session, owner, automation_id, *, idempotency_key):
    if not isinstance(idempotency_key, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", idempotency_key
    ):
        raise HTTPException(422, "Use an idempotency key of 1-100 safe characters")
    auto = await owned_automation(session, owner, automation_id, lock=True)
    existing = await session.scalar(
        select(AutomationRun).where(
            AutomationRun.automation_id == auto.id, AutomationRun.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return AutomationRunView.model_validate(existing)
    if (
        await session.scalar(
            select(AutomationRun.id)
            .where(
                AutomationRun.automation_id == auto.id, AutomationRun.status.in_(ACTIVE_STATUSES)
            )
            .limit(1)
        )
        is not None
    ):
        raise HTTPException(409, "Automation already has an active run")
    from backend.services.automations.dispatch import enqueue_automation_run

    row = await enqueue_automation_run(
        session,
        auto,
        await current_revision(session, auto),
        trigger="manual",
        idempotency_key=idempotency_key,
    )
    audit(session, owner, "run_now", row.id)
    await session.commit()
    return AutomationRunView.model_validate(row)


async def close_unknown_run(session, owner, run_id):
    _, row = await lock_run(session, owner, run_id)
    if row.status != "needs_reconciliation":
        raise HTTPException(409, "Only an unknown-outcome run can be closed manually")
    row.status, row.stop_reason, row.finished_at = "failed", "closed_without_replay", utcnow()
    row.result = {**row.result, "manually_closed": True, "usage_unknown": True}
    audit(session, owner, "closed_without_replay", row.id)
    from backend.services.automations.notifications import queue_result_notification

    await queue_result_notification(session, row.id)
    await session.commit()
    return AutomationRunView.model_validate(row)
