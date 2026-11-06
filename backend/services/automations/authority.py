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
    GoogleMutation,
    User,
)
from backend.models.automation import AutomationGrant, AutomationRevision, AutomationRun
from backend.services.automations.service import audit
from backend.services.google.errors import GoogleServiceError
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
                or (grant.action not in {"read_sheet", "read_doc"} and not selected.can_edit)
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
        if entry.tool.name.startswith(("google_", "artifact_"))
        or entry.capability
        and entry.capability.side_effect != "read_only"
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
    args, name = dict(request.arguments), request.name
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
    if not name.startswith("google_"):
        return False  # Raw Python and all other tools retain their own approval.
    connection, file_id, action, a1, pinned = None, None, None, None, None
    commits = {
        "google_sheets_append": {"append_rows"},
        "google_sheets_update": {"update_range", "set_formulas", "format_range"},
        "google_docs_create": {"create_doc"},
        "google_docs_edit": {"edit_doc"},
        "google_drive_upload": {"upload_artifact"},
    }
    try:
        if name in commits:
            row = await session.scalar(
                select(GoogleMutation).where(
                    GoogleMutation.id == UUID(str(args.get("proposal_id"))),
                    GoogleMutation.owner_id == context.user_id,
                    GoogleMutation.context_id == context.conversation_id,
                    GoogleMutation.run_id == context.run_id,
                )
            )
            if (
                row is None
                or row.kind not in commits[name]
                or row.digest != args.get("digest")
                or row.payload["preview"] != args.get("preview")
            ):
                return False
            connection, file_id, action = row.connection_id, row.file_id, row.kind
            a1, pinned = row.payload["data"].get("range_a1"), row.payload["data"].get("tab")
        elif name in {"google_sheets_prepare", "google_sheets_read", "google_docs_read"}:
            ref = args["ref"]
            if str(ref["context_id"]) != str(context.conversation_id):
                return False
            connection, file_id = UUID(str(ref["connection_id"])), ref["file_id"]
            if name == "google_sheets_prepare":
                action, a1 = args["change"]["kind"], args["change"]["range_a1"]
            else:
                action = "read_sheet" if name == "google_sheets_read" else "read_doc"
                a1 = args.get("range_a1")
        elif name == "google_docs_prepare":
            action = "create_doc" if args["kind"] == "create" else "edit_doc"
            ref = args.get("ref")
            connection = UUID(str(ref["connection_id"] if ref else args["connection_id"]))
            file_id = ref["file_id"] if ref else None
            if ref and str(ref["context_id"]) != str(context.conversation_id):
                return False
        elif name == "google_drive_prepare":
            action, connection = "upload_artifact", UUID(str(args["connection_id"]))
        else:
            return False
        for grant in authority.grants:
            exact = (grant.connection_id, grant.file_id, grant.action) == (
                connection,
                file_id,
                action,
            )
            fresh_output = False
            if (
                not exact
                and connection == grant.connection_id
                and grant.action == "create_doc"
                and grant.file_id is None
                and file_id
                and action in {"read_doc", "edit_doc"}
            ):
                # Creating a document includes composing its contents, but only
                # for a native Doc this exact run created and still owns access to.
                created = (
                    await session.scalars(
                        select(GoogleMutation).where(
                            GoogleMutation.run_id == context.run_id,
                            GoogleMutation.owner_id == context.user_id,
                            GoogleMutation.context_id == context.conversation_id,
                            GoogleMutation.connection_id == connection,
                            GoogleMutation.kind == "create_doc",
                            GoogleMutation.status == "completed",
                        )
                    )
                ).all()
                for output in created:
                    receipt = output.receipt or {}
                    pin = receipt.get("file_authority") or {}
                    output_ref = receipt.get("file_ref") or {}
                    if output_ref.get("file_id") != file_id or str(
                        output_ref.get("context_id")
                    ) != str(context.conversation_id):
                        continue
                    selected = await session.scalar(
                        select(GoogleFileGrant)
                        .where(
                            GoogleFileGrant.connection_id == connection,
                            GoogleFileGrant.owner_id == context.user_id,
                            GoogleFileGrant.file_id == file_id,
                        )
                        .execution_options(populate_existing=True)
                    )
                    fresh_output = bool(
                        selected
                        and not selected.revoked
                        and selected.can_edit
                        and selected.generation == pin.get("grant_generation")
                        and selected.connection_generation
                        == pin.get("connection_generation")
                        == grant.connection_generation
                    )
            if not (exact or fresh_output):
                continue
            if a1:
                if grant.sheet_id is None or not covers(grant, a1, append=action == "append_rows"):
                    continue
                title = tab_title(parse_range(grant.range_a1))
                if pinned is None:
                    from backend.schemas.google import GoogleFileRef

                    if google_service is None:
                        from backend.services.agent_context import google_tool_service

                        google_service = google_tool_service()
                    ref = GoogleFileRef(
                        connection_id=connection,
                        file_id=file_id,
                        context_id=context.conversation_id,
                    )
                    auth = await google_service.access.authorize(
                        session, context, ref, "read_sheet"
                    )
                    pinned = await google_service.sheets.resolve_tab(
                        auth, parse_range(a1), {"sheet_id": grant.sheet_id, "title": title}
                    )
                if pinned != {"sheet_id": grant.sheet_id, "title": title}:
                    continue
            await resolve_run_authority(session, agent_run_id=context.run_id)
            audit(
                session,
                context.user_id,
                "approved_by_automation_grant",
                authority.automation_run_id,
                tool=name,
                grant_id=str(grant.id),
                revision=str(authority.revision.id),
            )
            await session.commit()
            return True
        return False
    except (KeyError, TypeError, ValueError, GoogleServiceError):
        return False
