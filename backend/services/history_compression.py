import asyncio
import json
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.context_budget import (
    ContextBlock,
    ContextTokenBudget,
    estimate_tokens,
    measure_context_blocks,
)
from agent_core.context_middleware import ContextAssemblyMiddleware
from agent_core.history_compression import (
    HISTORY_SUMMARY_PROMPT,
    HistoryCompressionPolicy,
)
from agent_core.llm.deepseek import to_langchain_messages, to_model_messages
from agent_core.llm.types import TokenUsage
from backend.core.config import get_settings
from backend.database import SessionFactory
from backend.dependencies_llm import create_chat_model
from backend.models import Conversation, ConversationSummary, Message, ModelCall
from backend.services.usage_limits import add_usage


class HistoryCompressionError(RuntimeError):
    pass


def _summary_block(summary: ConversationSummary) -> ContextBlock:
    return ContextBlock(
        content=summary.content,
        source=(
            f"conversation_summary:{summary.id}:tier:{summary.tier}:"
            f"revision:{summary.revision}:version:{summary.summary_version}"
        ),
    )


async def _summarize(
    entries,
    *,
    output_tokens,
    source_chars,
    budget,
    user_id,
    conversation_id,
    user_message_id,
    version,
):
    """One paid call per compression. No automatic retry or extractive fallback."""
    if output_tokens < 1:
        raise HistoryCompressionError("Không còn chỗ trong ngân sách Memory + History để tóm tắt.")
    messages = [
        {"role": "system", "content": HISTORY_SUMMARY_PROMPT},
        {"role": "user", "content": json.dumps({"source": entries}, ensure_ascii=False)},
    ]
    prepared = ContextAssemblyMiddleware(budget).prepare(to_langchain_messages(messages))
    limit = min(output_tokens, budget.output_tokens, get_settings().deepseek_max_tokens)
    model = create_chat_model(max_tokens=limit)
    usage, parts, finish = TokenUsage(), [], None
    started = perf_counter()
    call = ModelCall(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        provider=model.provider,
        model_name=model.model,
        status="running",
        prompt_version=version,
        prompt_fingerprint=sha256(HISTORY_SUMMARY_PROMPT.encode()).hexdigest(),
    )
    try:
        async with SessionFactory() as audit:
            audit.add(call)
            await audit.commit()
            try:
                async with asyncio.timeout(get_settings().history_summary_timeout_seconds):
                    stream = model.stream(
                        to_model_messages(prepared.messages),
                        tools=None,
                        user_id=str(user_id),
                    )
                    async with aclosing(stream):
                        async for chunk in stream:
                            if chunk.usage is not None:
                                usage += chunk.usage
                            if chunk.tool_call_deltas:
                                raise HistoryCompressionError("Model tóm tắt không được gọi tool.")
                            parts.append(chunk.content)
                            if chunk.finish_reason is not None:
                                finish = chunk.finish_reason
                content = "".join(parts).strip()
                if finish != "stop" or not content:
                    raise HistoryCompressionError("Model chưa tạo được summary hoàn chỉnh.")
                if len(content) > source_chars:
                    raise HistoryCompressionError("Summary chưa ngắn hơn nội dung nguồn.")
                if estimate_tokens(content) > output_tokens:
                    raise HistoryCompressionError("Summary vượt phần ngân sách history còn lại.")
                call.status = "completed"
                return content
            except BaseException as exc:
                call.status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                call.error_message = "Không hoàn tất tóm tắt history."
                raise
            finally:
                call.prompt_tokens = usage.prompt_tokens
                call.completion_tokens = usage.completion_tokens
                call.total_tokens = usage.total_tokens
                call.cached_input_tokens = usage.cached_input_tokens
                call.cache_miss_input_tokens = usage.cache_miss_input_tokens
                call.latency_ms = round((perf_counter() - started) * 1000)
                call.finished_at = datetime.now(UTC)
                await audit.commit()
                if usage.total_tokens:
                    await add_usage(user_id, metric="llm_token", amount=usage.total_tokens)
    finally:
        await model.aclose()


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
    *,
    user_id: UUID,
    user_message_id: UUID,
    budget: ContextTokenBudget,
) -> HistoryContext:
    # Serialize summaries for this owner/conversation. NO KEY UPDATE allows the
    # independent audit insert to reference the conversation while the LLM runs.
    try:
        owned = await session.scalar(
            select(Conversation.id)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
            .with_for_update(key_share=True)
        )
        if owned is None:
            raise HistoryCompressionError("Không tìm thấy hội thoại để tóm tắt.")
        return await _prepare_locked(
            session,
            conversation_id,
            policy,
            user_id=user_id,
            user_message_id=user_message_id,
            budget=budget,
        )
    except asyncio.CancelledError:
        await session.rollback()
        raise
    except Exception as exc:
        await session.rollback()
        if isinstance(exc, HistoryCompressionError):
            raise
        raise HistoryCompressionError(
            "Không hoàn tất tóm tắt history bằng LLM. Hãy thử lại."
        ) from exc


async def _prepare_locked(
    session: AsyncSession,
    conversation_id: UUID,
    policy: HistoryCompressionPolicy,
    *,
    user_id: UUID,
    user_message_id: UUID,
    budget: ContextTokenBudget,
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

    final_raw_start = (
        raw_start
        + max(0, (len(messages) - raw_start - policy.recent_message_limit) // policy.batch_size)
        * policy.batch_size
    )
    retained_tokens = sum(
        4 + estimate_tokens({"role": m.role, "content": m.content})
        for m in messages[final_raw_start:]
    )

    async def summarize(entries, source_chars, replaced=()):
        blocks = [
            _summary_block(s)
            for s in current_summaries
            if s.superseded_by_id is None and s not in replaced
        ]
        # Reserve serialized block metadata as well as summary text. There is no
        # per-summary character ceiling: raw recent history and other active
        # summaries determine the actual available shared space.
        placeholder = ConversationSummary(
            id=uuid.uuid4(),
            tier=max((s.tier for s in replaced), default=0) + 1,
            revision=revision + 1,
            summary_version=policy.version,
            content="x",
        )
        overhead = (
            measure_context_blocks("history_summary", [*blocks, _summary_block(placeholder)]) - 1
        )
        available = budget.memory_history_tokens - retained_tokens - overhead
        if available < 1:
            raise HistoryCompressionError("Không đủ ngân sách chung để giữ history và summary.")
        return await _summarize(
            entries,
            output_tokens=min(available, max(1, estimate_tokens(entries) - 1)),
            source_chars=source_chars,
            budget=budget,
            user_id=user_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            version=policy.version,
        )

    while len(messages) - raw_start - policy.recent_message_limit >= policy.batch_size:
        batch = messages[raw_start : raw_start + policy.batch_size]
        source_char_count = sum(len(message.content) for message in batch)
        content = await summarize(
            [{"role": m.role, "content": m.content} for m in batch],
            source_char_count,
        )
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
            content = await summarize(
                [{"tier": s.tier, "summary": s.content} for s in children],
                sum(len(s.content) for s in children),
                replaced=children,
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
            # Insert the parent before writing children FK references to it;
            # the enclosing transaction still rolls back the whole compression.
            await session.flush()
            for child in children:
                child.superseded_by_id = parent.id
            current_summaries.append(parent)
            created_summary_count += 1
            merged = True
            break
        if not merged:
            break

    active_summaries = sorted(
        (summary for summary in current_summaries if summary.superseded_by_id is None),
        key=lambda summary: _summary_position(summary, positions),
    )
    summary_blocks = [_summary_block(s) for s in active_summaries]
    if (
        retained_tokens + measure_context_blocks("history_summary", summary_blocks)
        > budget.memory_history_tokens
    ):
        raise HistoryCompressionError("History hiện tại vượt ngân sách chung Memory + History.")
    await session.commit()
    return HistoryContext(
        messages=messages[raw_start:],
        summaries=summary_blocks,
        created_summary_count=created_summary_count,
    )
