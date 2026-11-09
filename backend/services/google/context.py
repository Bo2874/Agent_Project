"""Owner/run bridge for chat and background workers. Model arguments grant no authority."""

import json
from dataclasses import replace

from sqlalchemy import select

from agent_core.tools.authorization import check_authorization_once
from agent_core.tools.types import ToolAuthorizationError
from backend.core.config import get_settings
from backend.database import SessionFactory
from backend.models import (
    AgentRun,
    Conversation,
    GoogleConnection,
    GoogleFileGrant,
    GoogleMutation,
    GoogleWorkFile,
)
from backend.services.google.access import GoogleAccess, grant_view
from backend.services.google.credentials import GoogleCredentials
from backend.services.google.errors import GoogleServiceError


class GoogleContextService:
    def __init__(self, *, access=None, sessions=None):
        self.access = access or GoogleAccess(GoogleCredentials(get_settings()))
        self.sessions = sessions or SessionFactory

    async def check_run(self, session, context):
        run = await session.scalar(
            select(AgentRun)
            .join(Conversation, Conversation.id == AgentRun.conversation_id)
            .where(
                AgentRun.id == context.run_id,
                AgentRun.conversation_id == context.conversation_id,
                Conversation.user_id == context.user_id,
            )
        )
        if run is None or run.status in {"cancelled", "failed"}:
            raise GoogleServiceError(404, "Active owned Google run not found")

    async def pin(self, context):
        async with self.sessions() as session:
            await self.check_run(session, context)
            rows = await session.execute(
                select(GoogleFileGrant)
                .join(GoogleWorkFile, GoogleWorkFile.grant_id == GoogleFileGrant.id)
                .where(
                    GoogleWorkFile.owner_id == context.user_id,
                    GoogleWorkFile.context_id == context.conversation_id,
                    GoogleFileGrant.revoked.is_(False),
                    GoogleWorkFile.grant_generation == GoogleFileGrant.generation,
                )
            )
            refs = []
            for grant in rows.scalars():
                connection = await self.access.credentials.owned(
                    session, context.user_id, grant.connection_id
                )
                if (
                    connection.status == "connected"
                    and grant.connection_generation == connection.generation
                ):
                    refs.append(
                        {
                            **grant_view(grant, context.conversation_id),
                            "grant_generation": grant.generation,
                            "connection_generation": connection.generation,
                        }
                    )
        return replace(context, google_file_refs=tuple(refs))

    async def connection_context(self, context):
        async with self.sessions() as session:
            await self.check_run(session, context)
            rows = await session.scalars(
                select(GoogleConnection).where(
                    GoogleConnection.owner_id == context.user_id,
                    GoogleConnection.status == "connected",
                    GoogleConnection.offline_ready.is_(True),
                )
            )
            allowed = None
            from backend.models.automation import AutomationGrant, AutomationRun

            automation = await session.scalar(
                select(AutomationRun).where(AutomationRun.agent_run_id == context.run_id)
            )
            if automation is not None:
                allowed = set(
                    await session.scalars(
                        select(AutomationGrant.connection_id).where(
                            AutomationGrant.revision_id == automation.revision_id
                        )
                    )
                )
            return [
                {
                    "connection_id": str(row.id),
                    "account_name": row.account_name,
                    "account_email": row.account_email,
                    "generation": row.generation,
                }
                for row in rows
                if allowed is None or row.id in allowed
            ]

    async def guard(self, context, name):
        if not name.startswith("google_"):
            return
        await check_authorization_once(
            (
                id(self),
                context.user_id,
                context.run_id,
                context.conversation_id,
                json.dumps(list(context.google_file_refs), sort_keys=True, default=str),
            ),
            lambda: self.validate_authority(context),
        )

    async def validate_authority(self, context):
        try:
            async with self.sessions() as session:
                await self.check_run(session, context)
                created = await session.scalars(
                    select(GoogleMutation).where(
                        GoogleMutation.owner_id == context.user_id,
                        GoogleMutation.run_id == context.run_id,
                        GoogleMutation.context_id == context.conversation_id,
                        GoogleMutation.status == "completed",
                        GoogleMutation.kind.in_(["create_doc", "upload_artifact"]),
                    )
                )
                pins = list(context.google_file_refs)
                for row in created:
                    receipt = row.receipt or {}
                    if not receipt.get("file_ref") or not receipt.get("file_authority"):
                        raise GoogleServiceError(409, "Created Google file authority unavailable")
                    pins.append({**receipt["file_ref"], **receipt["file_authority"]})
                unique = {(str(pin["connection_id"]), pin["file_id"]): pin for pin in pins}
                await self.access.validate_pins(session, context, list(unique.values()))
        except GoogleServiceError:
            raise ToolAuthorizationError("google_authorization_changed") from None

    async def require_ref(self, session, context, ref):
        if any(
            str(item["connection_id"]) == str(ref.connection_id)
            and item["file_id"] == ref.file_id
            and str(item["context_id"]) == str(ref.context_id)
            for item in context.google_file_refs
        ):
            return
        # Only outputs created by this same run may extend its pinned input scope.
        created = await session.scalars(
            select(GoogleMutation).where(
                GoogleMutation.owner_id == context.user_id,
                GoogleMutation.run_id == context.run_id,
                GoogleMutation.context_id == context.conversation_id,
                GoogleMutation.status == "completed",
                GoogleMutation.kind.in_(["create_doc", "upload_artifact"]),
            )
        )
        if not any(
            row.receipt and row.receipt.get("file_ref") == ref.model_dump(mode="json")
            for row in created
        ):
            raise GoogleServiceError(404, "Google file is outside this run's pinned scope")
