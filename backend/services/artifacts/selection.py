from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Conversation
from backend.models.artifact import ArtifactVersion, ConversationArtifact
from backend.services.artifacts.service import published


async def owned_conversation(
    session: AsyncSession, owner: UUID, conversation_id: UUID, *, lock=False
):
    statement = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == owner
    )
    if lock:
        statement = statement.with_for_update()
    conversation = await session.scalar(statement)
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    return conversation


async def selected_versions(session: AsyncSession, owner: UUID, conversation_id: UUID):
    await owned_conversation(session, owner, conversation_id)
    return (
        await session.scalars(
            select(ArtifactVersion)
            .join(
                ConversationArtifact,
                ConversationArtifact.version_id == ArtifactVersion.id,
            )
            .where(
                ConversationArtifact.conversation_id == conversation_id,
                ConversationArtifact.owner_user_id == owner,
                ArtifactVersion.owner_user_id == owner,
            )
            .order_by(ArtifactVersion.created_at, ArtifactVersion.id)
        )
    ).all()


async def set_conversation_artifacts(session, owner, conversation_id, version_ids):
    await owned_conversation(session, owner, conversation_id, lock=True)
    ids = tuple(dict.fromkeys(version_ids))
    if len(ids) > 20:
        raise HTTPException(422, "Choose at most 20 working files.")
    rows = (
        (
            await session.scalars(
                select(ArtifactVersion).where(
                    ArtifactVersion.id.in_(ids),
                    ArtifactVersion.owner_user_id == owner,
                )
            )
        ).all()
        if ids
        else []
    )
    if len(rows) != len(ids):
        raise HTTPException(404, "Artifact version not found.")
    await session.execute(
        delete(ConversationArtifact).where(
            ConversationArtifact.conversation_id == conversation_id,
            ConversationArtifact.owner_user_id == owner,
        )
    )
    session.add_all(
        [
            ConversationArtifact(
                conversation_id=conversation_id, owner_user_id=owner, version_id=version_id
            )
            for version_id in ids
        ]
    )
    await session.commit()
    by_id = {row.id: row for row in rows}
    return tuple(published(by_id[version_id]) for version_id in ids)
