from datetime import UTC, datetime
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.graph.checkpointing import agent_thread_id
from backend.models import AgentRun, BackgroundJob, Conversation, Message


class ConversationBusyError(RuntimeError):
    pass


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


async def delete_conversation(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: UUID,
    checkpointer: BaseCheckpointSaver,
) -> bool:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        .with_for_update()
    )
    if conversation is None:
        return False
    runs = list(
        (
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.conversation_id == conversation_id)
                .with_for_update()
            )
        ).all()
    )
    if any(run.status in {"queued", "running", "waiting_approval"} for run in runs):
        raise ConversationBusyError("Hội thoại đang xử lý yêu cầu. Hãy đợi hoàn tất rồi xóa.")
    run_ids = [run.id for run in runs]
    if run_ids:
        jobs = list(
            (
                await session.scalars(
                    select(BackgroundJob)
                    .where(BackgroundJob.agent_run_id.in_(run_ids))
                    .with_for_update()
                )
            ).all()
        )
        if any(job.status in {"queued", "running"} for job in jobs):
            raise ConversationBusyError(
                "Hội thoại còn tác vụ nền đang chạy. Hãy dừng tác vụ trước."
            )
        for run_id in run_ids:
            await checkpointer.adelete_thread(agent_thread_id(run_id))
        await session.execute(delete(BackgroundJob).where(BackgroundJob.agent_run_id.in_(run_ids)))
    # Messages, summaries, runs, tool calls, routing traces and their memory sources cascade.
    await session.delete(conversation)
    await session.commit()
    return True


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
