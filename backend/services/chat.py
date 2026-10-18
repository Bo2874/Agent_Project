from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Conversation, Message


async def list_conversations(session: AsyncSession, user_id: UUID) -> list[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars())


async def get_owned_conversation(
    session: AsyncSession, conversation_id: UUID, user_id: UUID
) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def create_conversation(
    session: AsyncSession, user_id: UUID, title: str | None = None
) -> Conversation:
    conversation = Conversation(user_id=user_id)
    if title:
        conversation.title = title
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def list_messages(session: AsyncSession, conversation_id: UUID) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(result.scalars())


async def add_user_message(
    session: AsyncSession, conversation: Conversation, content: str
) -> Message:
    has_messages = await session.scalar(
        select(Message.id).where(Message.conversation_id == conversation.id).limit(1)
    )
    if has_messages is None and conversation.title == "Cuộc trò chuyện mới":
        conversation.title = content[:80] + ("…" if len(content) > 80 else "")

    conversation.updated_at = datetime.now(UTC)
    message = Message(conversation_id=conversation.id, role="user", content=content)
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message
