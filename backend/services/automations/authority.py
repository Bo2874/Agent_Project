"""Persisted revision authority, rechecked at every model/tool boundary."""

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import select

from agent_core.tools.types import ToolAuthorizationError
from backend.core.config import get_settings
from backend.models import (
    AgentRun,
    ArtifactVersion,
    Conversation,
    GoogleConnection,
    GoogleFileGrant,
    User,
)
from backend.models.automation import AutomationGrant, AutomationRevision, AutomationRun
from backend.services.automations.service import audit
from backend.services.google.ranges import column, parse_range


@dataclass
class RunAuthority:
    source: str
    owner: UUID
    revision: AutomationRevision | None = None
    grants: tuple = ()
    automation_run_id: UUID | None = None


async def resolve_run_authority(session, *, agent_run_id):
    record = (
        await session.execute(
            select(AgentRun, Conversation)
            .join(Conversation, Conversation.id == AgentRun.conversation_id)
            .where(AgentRun.id == agent_run_id)
        )
    ).first()
    if record is None:
        raise ToolAuthorizationError("automation_authority_changed")
    agent, conversation = record
    if conversation.source_kind == "chat":
        return RunAuthority("chat", conversation.user_id)
    row = await session.scalar(
        select(AutomationRun)
        .where(
            AutomationRun.agent_run_id == agent_run_id,
            AutomationRun.owner_id == conversation.user_id,
            AutomationRun.conversation_id == conversation.id,
        )
        .execution_options(populate_existing=True)
    )
    owner = await session.get(User, conversation.user_id)
    if (
        row is None
        or row.status not in {"running", "waiting_approval"}
        or owner is None
        or not owner.is_active
        or not get_settings().automations_enabled
    ):
        raise ToolAuthorizationError("automation_authority_changed")
    rev = await session.get(AutomationRevision, row.revision_id)
    grants = (
        await session.scalars(select(AutomationGrant).where(AutomationGrant.revision_id == rev.id))
    ).all()
    settings = get_settings()
    if (grants and not settings.google_connector_enabled) or (
        (rev.artifact_version_ids or rev.artifact_actions)
        and not settings.workspace_artifacts_enabled
    ):
        raise ToolAuthorizationError("automation_authority_changed")
    for grant in grants:
        conn = await session.scalar(
            select(GoogleConnection)
            .where(
                GoogleConnection.id == grant.connection_id,
                GoogleConnection.owner_id == row.owner_id,
            )
            .execution_options(populate_existing=True)
        )
        if (
            conn is None
            or conn.status != "connected"
            or not conn.offline_ready
            or conn.generation != grant.connection_generation
        ):
            raise ToolAuthorizationError("automation_authority_changed")
        if grant.file_id is not None:
            selected = await session.scalar(
                select(GoogleFileGrant)
                .where(
                    GoogleFileGrant.owner_id == row.owner_id,
                    GoogleFileGrant.connection_id == conn.id,
                    GoogleFileGrant.file_id == grant.file_id,
                )
                .execution_options(populate_existing=True)
            )
            if (
                selected is None
                or selected.revoked
                or selected.generation != grant.grant_generation
                or selected.connection_generation != conn.generation
                or (
                    grant.action not in {"read_sheet", "read_doc", "read_file"}
                    and not selected.can_edit
                )
            ):
                raise ToolAuthorizationError("automation_authority_changed")
    for identity in rev.artifact_version_ids:
        if (
            await session.scalar(
                select(ArtifactVersion.id).where(
                    ArtifactVersion.id == UUID(identity),
                    ArtifactVersion.owner_user_id == row.owner_id,
                )
            )
            is None
        ):
            raise ToolAuthorizationError("automation_authority_changed")
    return RunAuthority("automation", row.owner_id, rev, tuple(grants), row.id)


def scope_registry(registry):
    """Force host approval without changing shared registrations or allowlists."""
    result = registry.fork()
    old = [
        entry
        for entry in result.list_accessible_tools("admin")
        if entry.tool.name != "run_python_code"
        and (
            entry.tool.name.startswith(("google_", "artifact_"))
            or entry.capability
            and entry.capability.side_effect != "read_only"
        )
    ]
    result.replace_owned(
        old, [replace(entry, policy=replace(entry.policy, requires_approval=True)) for entry in old]
    )
    return result


def tab_title(bounds):
    return bounds.tab[1:-1].replace("''", "'") if bounds.tab.startswith("'") else bounds.tab


def covers(grant, requested, *, append=False):
    allowed, target = parse_range(grant.range_a1), parse_range(requested)
    if tab_title(allowed) != tab_title(target):
        return False
    if append:
        # This is the explicitly approved append table search range, not a
        # claim that Google OAuth restricts the physical appended row indices.
        return (allowed.start, allowed.end, allowed.first_column, allowed.last_column) == (
            target.start,
            target.end,
            target.first_column,
            target.last_column,
        )
    return allowed.start <= target.start <= target.end <= allowed.end and column(
        allowed.first_column
    ) <= column(target.first_column) <= column(target.last_column) <= column(allowed.last_column)


async def authorize_automation_call(session, context, request, *, google_service=None):
    authority = await resolve_run_authority(session, agent_run_id=context.run_id)
    if authority.source != "automation" or authority.owner != context.user_id:
        return False
    name = request.name
    artifact_action = {
        "artifact_inspect": "inspect",
        "artifact_create": "create",
        "artifact_edit": "edit",
    }.get(name)
    if artifact_action:
        allowed = artifact_action in authority.revision.artifact_actions
        if allowed:
            audit(
                session,
                context.user_id,
                "approved_by_automation_grant",
                authority.automation_run_id,
                tool=name,
                revision=str(authority.revision.id),
            )
            await session.commit()
        return allowed
    # Python/Google authority is checked by the host bridge against this revision.
    # Retired Google tools cannot be approved from historical checkpoints.
    return False
