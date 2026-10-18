import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from time import perf_counter
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.context_budget import ContextBlock, fit_context_blocks, measure_context_blocks
from agent_core.llm.types import ChatModel
from agent_core.memory import (
    MEMORY_EXTRACTION_PROMPT_VERSION,
    MEMORY_EXTRACTION_SYSTEM_PROMPT,
    MemoryCandidate,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
    classify_memory_sensitivity,
    comparable_memory_content,
    extract_memory_candidates,
    memory_retention_deadline,
    normalize_memory_content,
    normalize_memory_key,
    rank_relevant_memories,
)
from backend.core.config import get_settings
from backend.core.redaction import redact_text
from backend.database import SessionFactory
from backend.models import Conversation, Message, ModelCall, UserMemory


class MemorySourceOwnershipError(PermissionError):
    pass


class MemoryNotFoundError(LookupError):
    pass


class SensitiveMemoryRejectedError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MemoryWriteSummary:
    extracted: int = 0
    created: int = 0
    duplicates: int = 0
    conflicts_replaced: int = 0
    conflicts_kept: int = 0
    below_confidence: int = 0
    blocked_sensitive: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class MemoryContextResult:
    blocks: tuple[ContextBlock, ...]
    candidate_count: int
    relevant_count: int
    selected_count: int
    token_count: int


def _retention_deadline(observed_at: datetime, sensitivity: MemorySensitivity) -> datetime:
    settings = get_settings()
    return memory_retention_deadline(
        observed_at,
        sensitivity,
        normal_days=settings.memory_retention_normal_days,
        personal_days=settings.memory_retention_personal_days,
        sensitive_days=settings.memory_retention_sensitive_days,
    )


def _retention_is_active(memory: UserMemory, now: datetime) -> bool:
    return memory.is_pinned or memory.retention_until > now


async def list_user_memories(
    session: AsyncSession,
    user_id: UUID,
    *,
    now: datetime | None = None,
) -> list[UserMemory]:
    current_time = now or datetime.now(UTC)
    memories = list(
        (
            await session.scalars(
                select(UserMemory)
                .where(
                    UserMemory.user_id == user_id,
                    UserMemory.status == MemoryStatus.ACTIVE.value,
                    or_(
                        UserMemory.is_pinned.is_(True),
                        UserMemory.retention_until > current_time,
                    ),
                )
                .order_by(
                    UserMemory.is_pinned.desc(),
                    UserMemory.updated_at.desc(),
                    UserMemory.id,
                )
            )
        ).all()
    )
    return [
        memory
        for memory in memories
        if memory.user_id == user_id
        and memory.status == MemoryStatus.ACTIVE.value
        and _retention_is_active(memory, current_time)
    ]


async def get_owned_active_memory(
    session: AsyncSession,
    *,
    user_id: UUID,
    memory_id: UUID,
) -> UserMemory:
    memory = await session.scalar(
        select(UserMemory).where(
            UserMemory.id == memory_id,
            UserMemory.user_id == user_id,
            UserMemory.status == MemoryStatus.ACTIVE.value,
        )
    )
    if memory is None or memory.user_id != user_id or memory.status != MemoryStatus.ACTIVE.value:
        raise MemoryNotFoundError("Không tìm thấy memory")
    return memory


async def update_user_memory(
    session: AsyncSession,
    *,
    user_id: UUID,
    memory_id: UUID,
    content: str,
) -> UserMemory:
    current = await get_owned_active_memory(
        session,
        user_id=user_id,
        memory_id=memory_id,
    )
    normalized_content = normalize_memory_content(content)
    sensitivity = classify_memory_sensitivity(current.memory_key, normalized_content)
    if sensitivity == MemorySensitivity.RESTRICTED:
        raise SensitiveMemoryRejectedError(
            "Memory chứa mật khẩu, token hoặc thông tin xác thực nên không thể lưu"
        )
    if comparable_memory_content(current.content) == comparable_memory_content(normalized_content):
        return current

    observed_at = datetime.now(UTC)
    replacement = UserMemory(
        id=uuid.uuid4(),
        user_id=user_id,
        memory_type=current.memory_type,
        memory_key=current.memory_key,
        content=normalized_content,
        source_type=MemorySourceType.MANUAL.value,
        source_reference=f"user_edit:{current.id}",
        confidence=Decimal("1.000"),
        observed_at=observed_at,
        valid_from=current.valid_from,
        valid_until=current.valid_until,
        status=MemoryStatus.SUPERSEDED.value,
        sensitivity=sensitivity.value,
        retention_until=_retention_deadline(observed_at, sensitivity),
        is_pinned=current.is_pinned,
        superseded_by_id=current.id,
    )
    session.add(replacement)
    await session.flush()

    current.status = MemoryStatus.SUPERSEDED.value
    current.superseded_by_id = replacement.id
    await session.flush()

    replacement.status = MemoryStatus.ACTIVE.value
    replacement.superseded_by_id = None
    await session.commit()
    await session.refresh(replacement)
    return replacement


async def set_memory_pinned(
    session: AsyncSession,
    *,
    user_id: UUID,
    memory_id: UUID,
    is_pinned: bool,
) -> UserMemory:
    memory = await get_owned_active_memory(
        session,
        user_id=user_id,
        memory_id=memory_id,
    )
    memory.is_pinned = is_pinned
    await session.commit()
    await session.refresh(memory)
    return memory


async def delete_user_memory(
    session: AsyncSession,
    *,
    user_id: UUID,
    memory_id: UUID,
) -> None:
    memory = await get_owned_active_memory(
        session,
        user_id=user_id,
        memory_id=memory_id,
    )
    await session.delete(memory)
    await session.commit()


async def purge_expired_memories(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    user_id: UUID | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    conditions = [
        UserMemory.is_pinned.is_(False),
        UserMemory.retention_until <= current_time,
    ]
    if user_id is not None:
        conditions.append(UserMemory.user_id == user_id)
    result = await session.execute(delete(UserMemory).where(*conditions))
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def _is_memory_available(
    memory: UserMemory,
    *,
    user_id: UUID,
    now: datetime,
    excluded_source_message_id: UUID | None,
) -> bool:
    if memory.user_id != user_id or memory.status != MemoryStatus.ACTIVE.value:
        return False
    if excluded_source_message_id is not None and (
        memory.source_message_id == excluded_source_message_id
    ):
        return False
    if memory.valid_from is not None and memory.valid_from > now:
        return False
    if memory.valid_until is not None and memory.valid_until <= now:
        return False
    return _retention_is_active(memory, now)


async def retrieve_memory_context(
    session: AsyncSession,
    *,
    user_id: UUID,
    query: str,
    token_budget: int,
    min_confidence: Decimal,
    max_items: int,
    candidate_limit: int,
    excluded_source_message_id: UUID | None = None,
    now: datetime | None = None,
) -> MemoryContextResult:
    current_time = now or datetime.now(UTC)
    source_filter = (
        or_(
            UserMemory.source_message_id.is_(None),
            UserMemory.source_message_id != excluded_source_message_id,
        )
        if excluded_source_message_id is not None
        else True
    )
    memories = list(
        (
            await session.scalars(
                select(UserMemory)
                .where(
                    UserMemory.user_id == user_id,
                    UserMemory.status == MemoryStatus.ACTIVE.value,
                    UserMemory.confidence >= min_confidence,
                    or_(UserMemory.valid_from.is_(None), UserMemory.valid_from <= current_time),
                    or_(
                        UserMemory.valid_until.is_(None),
                        UserMemory.valid_until > current_time,
                    ),
                    or_(
                        UserMemory.is_pinned.is_(True),
                        UserMemory.retention_until > current_time,
                    ),
                    source_filter,
                )
                .order_by(
                    UserMemory.is_pinned.desc(),
                    UserMemory.confidence.desc(),
                    UserMemory.observed_at.desc(),
                )
                .limit(candidate_limit)
            )
        ).all()
    )
    available = [
        memory
        for memory in memories
        if memory.confidence >= min_confidence
        and _is_memory_available(
            memory,
            user_id=user_id,
            now=current_time,
            excluded_source_message_id=excluded_source_message_id,
        )
    ]
    ranked = rank_relevant_memories(available, query, now=current_time)
    blocks = [
        ContextBlock(
            content=json.dumps(
                {
                    "type": item.memory.memory_type,
                    "key": item.memory.memory_key,
                    "value": item.memory.content,
                    "confidence": str(item.memory.confidence),
                    "observed_at": item.memory.observed_at.isoformat(),
                    "sensitivity": item.memory.sensitivity,
                    "relevance_score": item.score,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            source=f"user_memory:{item.memory.id}",
        )
        for item in ranked[:max_items]
    ]
    selected = fit_context_blocks("memory", blocks, token_budget)
    return MemoryContextResult(
        blocks=tuple(selected),
        candidate_count=len(available),
        relevant_count=len(ranked),
        selected_count=len(selected),
        token_count=measure_context_blocks("memory", selected),
    )


async def load_relevant_memory_context(
    *,
    user_id: UUID,
    query: str,
    token_budget: int,
    min_confidence: Decimal,
    max_items: int,
    candidate_limit: int,
    excluded_source_message_id: UUID | None = None,
) -> MemoryContextResult:
    async with SessionFactory() as session:
        return await retrieve_memory_context(
            session,
            user_id=user_id,
            query=query,
            token_budget=token_budget,
            min_confidence=min_confidence,
            max_items=max_items,
            candidate_limit=candidate_limit,
            excluded_source_message_id=excluded_source_message_id,
        )


async def resolve_memory_candidates(
    session: AsyncSession,
    *,
    user_id: UUID,
    user_message_id: UUID,
    observed_at: datetime,
    candidates: tuple[MemoryCandidate, ...],
    min_confidence: Decimal,
) -> MemoryWriteSummary:
    if not candidates:
        return MemoryWriteSummary()

    owned_message_id = await session.scalar(
        select(Message.id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.id == user_message_id, Conversation.user_id == user_id)
    )
    if owned_message_id is None:
        raise MemorySourceOwnershipError("Message nguồn không thuộc user")

    created = 0
    duplicates = 0
    conflicts_replaced = 0
    conflicts_kept = 0
    below_confidence = 0
    blocked_sensitive = 0
    for candidate in candidates:
        if candidate.confidence < min_confidence:
            below_confidence += 1
            continue
        memory_key = normalize_memory_key(candidate.key)
        content = normalize_memory_content(candidate.content)
        sensitivity = classify_memory_sensitivity(memory_key, content)
        if sensitivity == MemorySensitivity.RESTRICTED:
            blocked_sensitive += 1
            continue
        retention_until = _retention_deadline(observed_at, sensitivity)
        existing = await session.scalar(
            select(UserMemory)
            .where(
                UserMemory.user_id == user_id,
                UserMemory.memory_type == candidate.memory_type.value,
                UserMemory.memory_key == memory_key,
                UserMemory.status == MemoryStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        if existing is not None and comparable_memory_content(
            existing.content
        ) == comparable_memory_content(content):
            existing.sensitivity = sensitivity.value
            if (
                getattr(existing, "retention_until", None) is None
                or existing.retention_until < retention_until
            ):
                existing.retention_until = retention_until
            duplicates += 1
            continue

        memory_id = uuid.uuid4()
        status = MemoryStatus.ACTIVE.value
        superseded_by_id = None
        if existing is not None and candidate.confidence < existing.confidence:
            status = MemoryStatus.SUPERSEDED.value
            superseded_by_id = existing.id
            conflicts_kept += 1
        elif existing is not None:
            existing.status = MemoryStatus.SUPERSEDED.value
            existing.superseded_by_id = memory_id
            conflicts_replaced += 1
        else:
            created += 1

        session.add(
            UserMemory(
                id=memory_id,
                user_id=user_id,
                memory_type=candidate.memory_type.value,
                memory_key=memory_key,
                content=content,
                source_type=MemorySourceType.USER_MESSAGE.value,
                source_message_id=user_message_id,
                source_reference=MEMORY_EXTRACTION_PROMPT_VERSION,
                confidence=candidate.confidence,
                observed_at=observed_at,
                status=status,
                sensitivity=sensitivity.value,
                retention_until=retention_until,
                superseded_by_id=superseded_by_id,
            )
        )

    return MemoryWriteSummary(
        extracted=len(candidates),
        created=created,
        duplicates=duplicates,
        conflicts_replaced=conflicts_replaced,
        conflicts_kept=conflicts_kept,
        below_confidence=below_confidence,
        blocked_sensitive=blocked_sensitive,
    )


async def extract_and_store_user_message_memories(
    *,
    model: ChatModel,
    user_id: UUID,
    conversation_id: UUID,
    user_message_id: UUID,
    user_message_content: str,
    observed_at: datetime,
    min_confidence: Decimal,
    timeout_seconds: float,
) -> MemoryWriteSummary:
    if (
        classify_memory_sensitivity("user.message", user_message_content)
        == MemorySensitivity.RESTRICTED
    ):
        return MemoryWriteSummary(extracted=1, blocked_sensitive=1)
    model_call_id = uuid.uuid4()
    started_at = perf_counter()
    prompt_fingerprint = sha256(MEMORY_EXTRACTION_SYSTEM_PROMPT.encode()).hexdigest()
    async with SessionFactory() as session:
        model_call = ModelCall(
            id=model_call_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            provider=model.provider,
            model_name=model.model,
            status="running",
            prompt_version=MEMORY_EXTRACTION_PROMPT_VERSION,
            prompt_fingerprint=prompt_fingerprint,
        )
        session.add(model_call)
        await session.commit()

        try:
            async with asyncio.timeout(timeout_seconds):
                extraction = await extract_memory_candidates(
                    model,
                    user_message_content,
                    user_id=user_id,
                )
            summary = await resolve_memory_candidates(
                session,
                user_id=user_id,
                user_message_id=user_message_id,
                observed_at=observed_at,
                candidates=extraction.candidates,
                min_confidence=min_confidence,
            )
            usage = extraction.usage
            model_call.status = "completed"
            model_call.prompt_tokens = usage.prompt_tokens
            model_call.completion_tokens = usage.completion_tokens
            model_call.total_tokens = usage.total_tokens
            model_call.cached_input_tokens = usage.cached_input_tokens
            model_call.cache_miss_input_tokens = usage.cache_miss_input_tokens
            model_call.latency_ms = round((perf_counter() - started_at) * 1000)
            model_call.finished_at = datetime.now(UTC)
            await session.commit()
            return MemoryWriteSummary(
                extracted=summary.extracted,
                created=summary.created,
                duplicates=summary.duplicates,
                conflicts_replaced=summary.conflicts_replaced,
                conflicts_kept=summary.conflicts_kept,
                below_confidence=summary.below_confidence,
                blocked_sensitive=summary.blocked_sensitive,
                total_tokens=usage.total_tokens,
            )
        except asyncio.CancelledError:
            await session.rollback()
            cancelled_call = await session.get(ModelCall, model_call_id)
            if cancelled_call is not None:
                cancelled_call.status = "cancelled"
                cancelled_call.error_message = "Memory extraction bị hủy"
                cancelled_call.latency_ms = round((perf_counter() - started_at) * 1000)
                cancelled_call.finished_at = datetime.now(UTC)
                await session.commit()
            raise
        except Exception as exc:
            await session.rollback()
            failed_call = await session.get(ModelCall, model_call_id)
            if failed_call is not None:
                failed_call.status = "failed"
                failed_call.error_message = redact_text(exc, max_length=500)
                failed_call.latency_ms = round((perf_counter() - started_at) * 1000)
                failed_call.finished_at = datetime.now(UTC)
                await session.commit()
            raise
