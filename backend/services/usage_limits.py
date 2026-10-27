from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from agent_core.graph import AgentLoopEvent, AgentQuotaLimitError
from agent_core.graph.budgeting import UsageReservation
from backend.database import SessionFactory
from backend.models import UsageCounter

UsageMetric = Literal["agent_request", "agent_run", "llm_token", "tool_call"]
MINUTE_SECONDS = 60
DAY_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class UsageLimit:
    metric: UsageMetric
    limit: int
    window_seconds: int
    amount: int = 1

    def __post_init__(self) -> None:
        if self.limit < 1 or self.window_seconds < 1 or not 1 <= self.amount <= self.limit:
            raise ValueError("UsageLimit không hợp lệ")


class UsageLimitExceeded(RuntimeError):
    def __init__(
        self,
        *,
        metric: UsageMetric,
        limit: int,
        retry_after_seconds: int,
    ) -> None:
        messages = {
            "agent_request": "Bạn gửi yêu cầu quá nhanh. Vui lòng thử lại sau.",
            "agent_run": "Bạn đã dùng hết số agent run trong ngày.",
            "llm_token": "Bạn đã dùng hết quota token trong ngày.",
            "tool_call": "Bạn đã dùng hết quota tool call trong ngày.",
        }
        super().__init__(messages[metric])
        self.metric = metric
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds


def _window(now: datetime, seconds: int) -> tuple[datetime, int]:
    timestamp = int(now.timestamp())
    elapsed = timestamp % seconds
    start = datetime.fromtimestamp(timestamp - elapsed, UTC)
    return start, max(1, seconds - elapsed)


async def consume_usage_limits(
    user_id: UUID, limits: tuple[UsageLimit, ...], *, observed_at: datetime | None = None
) -> None:
    now = observed_at or datetime.now(UTC)
    async with SessionFactory() as session:
        for item in limits:
            window_start, retry_after = _window(now, item.window_seconds)
            statement = (
                insert(UsageCounter)
                .values(
                    user_id=user_id,
                    metric=item.metric,
                    window_start=window_start,
                    window_seconds=item.window_seconds,
                    value=item.amount,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[
                        UsageCounter.user_id,
                        UsageCounter.metric,
                        UsageCounter.window_start,
                    ],
                    set_={
                        "value": UsageCounter.value + item.amount,
                        "updated_at": now,
                    },
                    where=UsageCounter.value + item.amount <= item.limit,
                )
                .returning(UsageCounter.value)
            )
            value = (await session.execute(statement)).scalar_one_or_none()
            if value is None:
                await session.rollback()
                raise UsageLimitExceeded(
                    metric=item.metric,
                    limit=item.limit,
                    retry_after_seconds=retry_after,
                )
        await session.commit()


async def add_usage(
    user_id: UUID,
    *,
    metric: UsageMetric,
    amount: int,
    window_seconds: int = DAY_SECONDS,
) -> int:
    if amount < 1:
        raise ValueError("amount phải lớn hơn hoặc bằng 1")
    now = datetime.now(UTC)
    window_start, _ = _window(now, window_seconds)
    statement = (
        insert(UsageCounter)
        .values(
            user_id=user_id,
            metric=metric,
            window_start=window_start,
            window_seconds=window_seconds,
            value=amount,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                UsageCounter.user_id,
                UsageCounter.metric,
                UsageCounter.window_start,
            ],
            set_={"value": UsageCounter.value + amount, "updated_at": now},
        )
        .returning(UsageCounter.value)
    )
    async with SessionFactory() as session:
        value = (await session.execute(statement)).scalar_one()
        await session.commit()
        return int(value)


async def get_current_usage(
    user_id: UUID,
    *,
    metric: UsageMetric,
    window_seconds: int,
) -> int:
    window_start, _ = _window(datetime.now(UTC), window_seconds)
    async with SessionFactory() as session:
        value = await session.scalar(
            select(UsageCounter.value).where(
                UsageCounter.user_id == user_id,
                UsageCounter.metric == metric,
                UsageCounter.window_start == window_start,
            )
        )
        return int(value or 0)


async def settle_reserved_usage(user_id, *, window_start, reserved, actual):
    """Settle in the original day, including calls which finish across midnight."""
    async with SessionFactory() as session:
        statement = (
            update(UsageCounter)
            .where(
                UsageCounter.user_id == user_id,
                UsageCounter.metric == "llm_token",
                UsageCounter.window_start == window_start,
            )
            .values(value=UsageCounter.value + actual - reserved, updated_at=datetime.now(UTC))
        )
        await session.execute(statement)
        await session.commit()


async def ensure_usage_available(
    user_id: UUID,
    *,
    metric: UsageMetric,
    limit: int,
    window_seconds: int,
) -> None:
    current = await get_current_usage(
        user_id,
        metric=metric,
        window_seconds=window_seconds,
    )
    if current >= limit:
        _, retry_after = _window(datetime.now(UTC), window_seconds)
        raise UsageLimitExceeded(
            metric=metric,
            limit=limit,
            retry_after_seconds=retry_after,
        )


class AgentQuotaGuard:
    def __init__(
        self,
        *,
        user_id: UUID,
        token_limit: int,
        tool_call_limit: int,
    ) -> None:
        self.user_id = user_id
        self.token_limit = token_limit
        self.tool_call_limit = tool_call_limit
        self._reservation = ContextVar(f"quota_receipt_{id(self)}", default=None)

    @property
    def active_reservation(self):
        return self._reservation.get()

    @asynccontextmanager
    async def reserve_tokens(self, amount):
        if self.active_reservation is not None:
            raise RuntimeError("Nested root token reservations are not supported")
        now = datetime.now(UTC)
        window_start, _ = _window(now, DAY_SECONDS)
        try:
            await consume_usage_limits(
                self.user_id,
                (
                    UsageLimit(
                        metric="llm_token",
                        limit=self.token_limit,
                        window_seconds=DAY_SECONDS,
                        amount=amount,
                    ),
                ),
                observed_at=now,
            )
        except (UsageLimitExceeded, ValueError) as exc:
            raise AgentQuotaLimitError("Không đủ quota token để dự trù lời gọi tiếp theo.") from exc
        receipt = UsageReservation()
        token = self._reservation.set(receipt)
        try:
            yield receipt
        finally:
            self._reservation.reset(token)
            # Unknown billing/crash retains the conservative reservation until day end.
            # Do not refund an API request simply because its response was lost.
            settled = receipt.actual
            if settled is None and receipt.known_tokens > amount:
                settled = receipt.known_tokens
            if settled is not None:
                await settle_reserved_usage(
                    self.user_id, window_start=window_start, reserved=amount, actual=settled
                )

    async def before_execution(self, action: Literal["model", "tool"]) -> None:
        if action == "tool":
            try:
                await consume_usage_limits(
                    self.user_id,
                    (
                        UsageLimit(
                            metric="tool_call",
                            limit=self.tool_call_limit,
                            window_seconds=DAY_SECONDS,
                        ),
                    ),
                )
            except UsageLimitExceeded as exc:
                raise AgentQuotaLimitError(str(exc)) from exc
            return

        if self.active_reservation is not None:
            return
        try:
            await ensure_usage_available(
                self.user_id,
                metric="llm_token",
                limit=self.token_limit,
                window_seconds=DAY_SECONDS,
            )
        except UsageLimitExceeded as exc:
            raise AgentQuotaLimitError(str(exc)) from exc

    async def record_event(self, event: AgentLoopEvent) -> None:
        if (
            event.kind not in {"model_completed", "model_failed", "tool_completed"}
            or event.usage is None
        ):
            return
        if self.active_reservation is not None:
            self.active_reservation.actual = (
                self.active_reservation.actual or 0
            ) + event.usage.total_tokens
            return
        if event.usage.total_tokens > 0:
            await self.record_tokens(event.usage.total_tokens)

    async def record_tokens(self, amount: int) -> None:
        if amount > 0:
            await add_usage(self.user_id, metric="llm_token", amount=amount)
