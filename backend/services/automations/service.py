"""Owner-scoped configuration and immutable execution snapshots."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from backend.models import ArtifactVersion, GoogleConnection, GoogleFileGrant
from backend.models.automation import Automation, AutomationGrant, AutomationRevision, AutomationRun
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
    return AutomationRunView.model_validate(row)
