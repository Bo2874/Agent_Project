"""Durable prepare/approve/sent/outcome journal. A sent write is never automatically retried."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from agent_core.tools.approval import approval_fingerprint
from backend.models import GoogleFileGrant, GoogleMutation, GoogleWorkFile
from backend.schemas.google import GoogleWritePreview, GoogleWriteProposal, GoogleWriteReceipt
from backend.services.google.errors import GoogleServiceError


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def proposal_view(row):
    return GoogleWriteProposal(
        id=row.id,
        digest=row.digest,
        snapshot_hash=row.snapshot_hash,
        expires_at=row.expires_at,
        preview=GoogleWritePreview.model_validate(row.payload["preview"]),
    )


def unknown():
    return GoogleServiceError(
        409,
        "Google write outcome is unknown; reconcile before any new write. "
        "Do not retry automatically.",
        "google_outcome_unknown",
    )


class GoogleMutations:
    def __init__(self, access, sessions):
        self.access, self.sessions = access, sessions

    async def unresolved(self, session, owner, connection_id, file_id, *, exclude=None):
        pending = await session.scalar(
            select(GoogleMutation.id)
            .where(
                GoogleMutation.owner_id == owner,
                GoogleMutation.connection_id == connection_id,
                GoogleMutation.file_id == file_id,
                GoogleMutation.status.in_(["sent", "unknown"]),
                *([GoogleMutation.id != exclude] if exclude else []),
            )
            .limit(1)
        )
        if pending is not None:
            raise unknown()

    async def prepare(
        self,
        context,
        *,
        connection_id,
        file_id,
        connection_generation,
        grant_generation,
        kind,
        request,
        data,
        snapshot_hash,
        preview,
    ):
        key = fingerprint(
            [str(context.run_id), context.task_id, context.operation_key or str(uuid4()), kind]
        )
        payload = {"request": request, "data": data, "preview": preview.model_dump(mode="json")}
        digest = fingerprint(
            {
                "owner": str(context.user_id),
                "context": str(context.conversation_id),
                "connection": str(connection_id),
                "file": file_id,
                "generation": [connection_generation, grant_generation],
                "snapshot": snapshot_hash,
                "payload": payload,
            }
        )
        async with self.sessions() as session:
            existing = await session.scalar(
                select(GoogleMutation).where(
                    GoogleMutation.owner_id == context.user_id, GoogleMutation.operation_key == key
                )
            )
            if existing is None:
                await self.unresolved(session, context.user_id, connection_id, file_id)
                row = GoogleMutation(
                    id=uuid4(),
                    owner_id=context.user_id,
                    connection_id=connection_id,
                    context_id=context.conversation_id,
                    run_id=context.run_id,
                    operation_key=key,
                    kind=kind,
                    file_id=file_id,
                    grant_generation=grant_generation,
                    connection_generation=connection_generation,
                    digest=digest,
                    snapshot_hash=snapshot_hash,
                    payload=payload,
                    status="prepared",
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                )
                session.add(row)
                try:
                    await session.commit()
                    return proposal_view(row)
                except IntegrityError:
                    await session.rollback()
                    existing = await session.scalar(
                        select(GoogleMutation).where(
                            GoogleMutation.owner_id == context.user_id,
                            GoogleMutation.operation_key == key,
                        )
                    )
            if (
                existing is None
                or existing.context_id != context.conversation_id
                or existing.connection_id != connection_id
                or existing.file_id != file_id
                or existing.payload["request"] != request
            ):
                raise GoogleServiceError(
                    409, "Google operation key conflicts with a different request"
                )
            return proposal_view(existing)

    async def owned(self, session, context, identity):
        row = await session.scalar(
            select(GoogleMutation)
            .where(
                GoogleMutation.id == UUID(str(identity)),
                GoogleMutation.owner_id == context.user_id,
                GoogleMutation.context_id == context.conversation_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None or row.run_id != context.run_id:
            raise GoogleServiceError(404, "Google write proposal not found")
        return row

    async def short_authority(self, session, context, row):
        connection = await self.access.credentials.owned(
            session, context.user_id, row.connection_id, lock=True
        )
        if connection.status != "connected" or connection.generation != row.connection_generation:
            raise GoogleServiceError(409, "Google connection changed; prepare a new approval")
        await self.unresolved(
            session, context.user_id, row.connection_id, row.file_id, exclude=row.id
        )
        await self.access.conversation(session, context.user_id, context.conversation_id)
        if row.file_id is not None:
            grant = await session.scalar(
                select(GoogleFileGrant)
                .where(
                    GoogleFileGrant.owner_id == context.user_id,
                    GoogleFileGrant.connection_id == row.connection_id,
                    GoogleFileGrant.file_id == row.file_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            selection = (
                await session.get(
                    GoogleWorkFile, (context.conversation_id, grant.id), populate_existing=True
                )
                if grant
                else None
            )
            if (
                grant is None
                or grant.revoked
                or grant.generation != row.grant_generation
                or selection is None
                or selection.grant_generation != row.grant_generation
                or grant.connection_generation != row.connection_generation
            ):
                raise GoogleServiceError(409, "Google file scope revoked; prepare a new approval")

    async def mark_unknown(self, identity):
        async with self.sessions() as session:
            await session.execute(
                update(GoogleMutation)
                .where(GoogleMutation.id == identity, GoogleMutation.status == "sent")
                .values(status="unknown")
            )
            await session.commit()

    async def execute(
        self,
        context,
        *,
        proposal_id,
        digest,
        preview,
        tool_name,
        authorize,
        snapshot,
        write,
        automatic=False,
    ):
        preview = GoogleWritePreview.model_validate(preview).model_dump(mode="json")
        arguments = {"proposal_id": str(proposal_id), "digest": digest, "preview": preview}
        if not automatic and context.approved_call != (
            tool_name,
            approval_fingerprint(tool_name, arguments),
        ):
            raise GoogleServiceError(
                403,
                "Host approval required for this exact Google proposal",
                "google_approval_required",
            )
        async with self.sessions() as session:
            row = await self.owned(session, context, proposal_id)
            if row.digest != digest or row.payload["preview"] != preview:
                raise GoogleServiceError(409, "Google approval payload changed")
            auth = await authorize(session, row)
            row = await self.owned(session, context, proposal_id)
            if row.status == "completed":
                return GoogleWriteReceipt.model_validate(row.receipt)
            if row.status in {"sent", "unknown"}:
                raise unknown()
            if row.status == "rejected" or row.expires_at <= datetime.now(UTC):
                raise GoogleServiceError(409, "Google proposal expired; prepare a new approval")
            await session.execute(
                update(GoogleMutation)
                .where(GoogleMutation.id == row.id, GoogleMutation.status == "prepared")
                .values(status="approved")
            )
            await session.commit()
            current_hash = await snapshot(row, auth)
            if current_hash != row.snapshot_hash:
                await session.execute(
                    update(GoogleMutation)
                    .where(
                        GoogleMutation.id == row.id,
                        GoogleMutation.status.in_(["prepared", "approved"]),
                    )
                    .values(status="rejected")
                )
                await session.commit()
                raise GoogleServiceError(
                    409, "Google snapshot changed; prepare and approve a new proposal"
                )
            await self.short_authority(session, context, row)
            if automatic:
                from backend.services.automations.authority import resolve_run_authority
                from backend.services.run_events import fence

                await fence(session)
                authority = await resolve_run_authority(session, agent_run_id=context.run_id)
                if authority.owner != context.user_id:
                    raise GoogleServiceError(403, "Google owner scope changed")
            claimed = await session.scalar(
                update(GoogleMutation)
                .where(
                    GoogleMutation.id == row.id, GoogleMutation.status.in_(["prepared", "approved"])
                )
                .values(status="sent")
                .returning(GoogleMutation.id)
            )
            await session.commit()  # Release all DB locks before Google write RPC.
            if claimed is None:
                current = await self.owned(session, context, row.id)
                if current.status == "completed":
                    return GoogleWriteReceipt.model_validate(current.receipt)
                raise unknown()
            identity = row.id  # Rollback expires ORM attributes even with expire_on_commit=False.
            try:
                data = await write(row, auth, session)
                if data.get("file_ref"):
                    ref = data["file_ref"]
                    grant = await session.scalar(
                        select(GoogleFileGrant).where(
                            GoogleFileGrant.owner_id == context.user_id,
                            GoogleFileGrant.connection_id == row.connection_id,
                            GoogleFileGrant.file_id == ref["file_id"],
                        )
                    )
                    if grant is None or grant.revoked:
                        raise GoogleServiceError(409, "Created Google file authority unavailable")
                    data["file_authority"] = {
                        "grant_generation": grant.generation,
                        "connection_generation": grant.connection_generation,
                        "mime_type": grant.mime_type,
                    }
                receipt = GoogleWriteReceipt(operation_id=row.id, **data)
                await session.execute(
                    update(GoogleMutation)
                    .where(GoogleMutation.id == row.id)
                    .values(status="completed", receipt=receipt.model_dump(mode="json"))
                )
                await session.commit()
                return receipt
            except BaseException as error:
                await session.rollback()
                if isinstance(error, GoogleServiceError) and error.code == "google_conflict":
                    # An explicit revision precondition rejection is a confirmed
                    # no-write response, unlike a lost response or malformed success.
                    await session.execute(
                        update(GoogleMutation)
                        .where(GoogleMutation.id == identity, GoogleMutation.status == "sent")
                        .values(status="rejected", receipt={"error_code": "google_conflict"})
                    )
                    await session.commit()
                    raise
                await asyncio.shield(self.mark_unknown(identity))
                if isinstance(error, asyncio.CancelledError):
                    raise
                # A successful COMMIT whose acknowledgement was lost is still final.
                saved = await self.owned(session, context, proposal_id)
                if saved.status == "completed":
                    return GoogleWriteReceipt.model_validate(saved.receipt)
                raise unknown() from None
