"""Durable cards use verified database versions, never IDs extracted from model prose."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from artifact_runtime.protocol import PublishedArtifact
from backend.models import AgentRun, Message
from backend.models.artifact import ArtifactVersion, MessageArtifact
from backend.services.artifacts.selection import owned_conversation
from backend.services.artifacts.service import published

OUTPUT_TOOLS = frozenset(
    {"artifact_create", "artifact_edit", "run_artifact_python", "artifact_rebuild_pdf_text_only"}
)


async def validated_refs(session, owner, conversation_id, refs, *, run_id=None):
    await owned_conversation(session, owner, conversation_id)
    parsed = tuple(PublishedArtifact.model_validate(ref) for ref in refs)
    ids = tuple(dict.fromkeys(ref.version_id for ref in parsed))
    if len(ids) > 20:
        raise ValueError("Too many file references")
    rows = (
        (
            await session.scalars(
                select(ArtifactVersion).where(
                    ArtifactVersion.id.in_(ids), ArtifactVersion.owner_user_id == owner
                )
            )
        ).all()
        if ids
        else []
    )
    by_id = {row.id: row for row in rows}
    for ref in parsed:
        row = by_id.get(ref.version_id)
        if row is None or published(row) != ref or (run_id and row.source_run_id != run_id):
            raise HTTPException(404, "Artifact reference not found.")
    if run_id:
        run = await session.scalar(
            select(AgentRun.id).where(
                AgentRun.id == run_id, AgentRun.conversation_id == conversation_id
            )
        )
        if run is None:
            raise HTTPException(404, "Artifact run not found.")
    return tuple(published(by_id[id]) for id in ids)


async def bind_message_artifacts(session, owner, conversation_id, message_id, refs):
    verified = await validated_refs(session, owner, conversation_id, refs)
    message = await session.scalar(
        select(Message.id).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
        )
    )
    if message is None:
        raise HTTPException(404, "Message not found.")
    if verified:
        await session.execute(
            insert(MessageArtifact)
            .values(
                [
                    {
                        "message_id": message_id,
                        "version_id": ref.version_id,
                        "conversation_id": conversation_id,
                        "owner_user_id": owner,
                    }
                    for ref in verified
                ]
            )
            .on_conflict_do_nothing()
        )
    return verified


async def run_artifacts(session, owner, conversation_id, run_id):
    await owned_conversation(session, owner, conversation_id)
    return tuple(
        published(row)
        for row in (
            await session.scalars(
                select(ArtifactVersion)
                .join(AgentRun, AgentRun.id == ArtifactVersion.source_run_id)
                .where(
                    ArtifactVersion.owner_user_id == owner,
                    AgentRun.id == run_id,
                    AgentRun.conversation_id == conversation_id,
                )
                .order_by(ArtifactVersion.created_at, ArtifactVersion.version_number)
            )
        ).all()
    )


async def message_artifacts(session, owner, conversation_id, message_ids):
    await owned_conversation(session, owner, conversation_id)
    rows = (
        await session.execute(
            select(MessageArtifact.message_id, ArtifactVersion)
            .join(ArtifactVersion, ArtifactVersion.id == MessageArtifact.version_id)
            .where(
                MessageArtifact.owner_user_id == owner,
                MessageArtifact.conversation_id == conversation_id,
                MessageArtifact.message_id.in_(message_ids),
                ArtifactVersion.owner_user_id == owner,
            )
            .order_by(ArtifactVersion.created_at, ArtifactVersion.version_number)
        )
    ).all()
    result: dict[UUID, list[PublishedArtifact]] = {}
    for message_id, version in rows:
        result.setdefault(message_id, []).append(published(version))
    return result
