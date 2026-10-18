import uuid
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.context_budget import ContextBlock
from agent_core.history_compression import (
    HistoryCompressionPolicy,
    merge_summary_contents,
    summarize_messages,
)
from backend.models import ConversationSummary, Message


@dataclass(frozen=True, slots=True)
class HistoryContext:
    messages: list[Message]
    summaries: list[ContextBlock]
    created_summary_count: int = 0


def _message_position(messages: list[Message]) -> dict[UUID, int]:
    return {message.id: index for index, message in enumerate(messages)}


def _summary_position(
    summary: ConversationSummary,
    positions: dict[UUID, int],
) -> int:
    message_id = summary.source_start_message_id
    return positions.get(message_id, len(positions)) if message_id is not None else len(positions)


async def prepare_history_context(
    session: AsyncSession,
    conversation_id: UUID,
    policy: HistoryCompressionPolicy,
) -> HistoryContext:
    messages = list(
        (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    )
    summaries = list(
        (
            await session.scalars(
                select(ConversationSummary)
                .where(ConversationSummary.conversation_id == conversation_id)
                .order_by(ConversationSummary.revision)
            )
        ).all()
    )
    positions = _message_position(messages)
    current_summaries = [
        summary for summary in summaries if summary.summary_version == policy.version
    ]
    latest_tier_one_end = max(
        (
            positions[summary.source_end_message_id]
            for summary in current_summaries
            if summary.tier == 1 and summary.source_end_message_id in positions
        ),
        default=-1,
    )
    raw_start = latest_tier_one_end + 1
    revision = max((summary.revision for summary in summaries), default=0)
    created_summary_count = 0

    while len(messages) - raw_start - policy.recent_message_limit >= policy.batch_size:
        batch = messages[raw_start : raw_start + policy.batch_size]
        source_char_count = sum(len(message.content) for message in batch)
        content = summarize_messages(batch, max_chars=policy.max_summary_chars)
        revision += 1
        summary = ConversationSummary(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            tier=1,
            revision=revision,
            summary_version=policy.version,
            content=content,
            source_start_message_id=batch[0].id,
            source_end_message_id=batch[-1].id,
            source_message_count=len(batch),
            source_char_count=source_char_count,
            summary_char_count=len(content),
        )
        session.add(summary)
        current_summaries.append(summary)
        raw_start += len(batch)
        created_summary_count += 1

    while True:
        active = [summary for summary in current_summaries if summary.superseded_by_id is None]
        merged = False
        for tier in sorted({summary.tier for summary in active}):
            candidates = sorted(
                (summary for summary in active if summary.tier == tier),
                key=lambda summary: _summary_position(summary, positions),
            )
            if len(candidates) < policy.merge_factor:
                continue
            children = candidates[: policy.merge_factor]
            content = merge_summary_contents(
                children,
                max_chars=policy.max_summary_chars,
            )
            revision += 1
            parent = ConversationSummary(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                tier=tier + 1,
                revision=revision,
                summary_version=policy.version,
                content=content,
                source_start_message_id=children[0].source_start_message_id,
                source_end_message_id=children[-1].source_end_message_id,
                source_message_count=sum(child.source_message_count for child in children),
                source_char_count=sum(child.source_char_count for child in children),
                summary_char_count=len(content),
            )
            session.add(parent)
            for child in children:
                child.superseded_by_id = parent.id
            current_summaries.append(parent)
            created_summary_count += 1
            merged = True
            break
        if not merged:
            break

    if created_summary_count:
        await session.commit()

    active_summaries = sorted(
        (summary for summary in current_summaries if summary.superseded_by_id is None),
        key=lambda summary: _summary_position(summary, positions),
    )
    summary_blocks = [
        ContextBlock(
            content=summary.content,
            source=(
                f"conversation_summary:{summary.id}:tier:{summary.tier}:"
                f"revision:{summary.revision}:version:{summary.summary_version}"
            ),
        )
        for summary in active_summaries
    ]
    return HistoryContext(
        messages=messages[raw_start:],
        summaries=summary_blocks,
        created_summary_count=created_summary_count,
    )
