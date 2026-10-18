from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from langgraph.store.base import BaseStore, Op, Result, SearchItem, SearchOp

from backend.services.memory import MemoryContextResult, load_relevant_memory_context

MemoryLoader = Callable[..., Awaitable[MemoryContextResult]]


@dataclass(frozen=True, slots=True)
class UserMemorySearchPolicy:
    token_budget: int
    min_confidence: Decimal
    max_items: int
    candidate_limit: int
    excluded_source_message_id: UUID | None = None


class ScopedUserMemoryStore(BaseStore):
    """Read-only LangGraph Store view over one user's PostgreSQL memories."""

    def __init__(
        self,
        *,
        user_id: UUID,
        policy: UserMemorySearchPolicy,
        loader: MemoryLoader = load_relevant_memory_context,
    ) -> None:
        self.user_id = user_id
        self.policy = policy
        self._loader = loader
        self.namespace = ("user_memories", str(user_id))

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        del ops
        raise RuntimeError("ScopedUserMemoryStore chỉ hỗ trợ API async")

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        results: list[Result] = []
        for operation in ops:
            if not isinstance(operation, SearchOp):
                raise PermissionError("User memory Store chỉ cho phép tìm kiếm")
            results.append(await self._search(operation))
        return results

    async def _search(self, operation: SearchOp) -> list[SearchItem]:
        if operation.namespace_prefix != self.namespace:
            raise PermissionError("Không được truy cập memory namespace của user khác")
        if operation.filter is not None:
            raise ValueError("User memory Store không nhận filter tùy ý")
        requested = operation.offset + operation.limit
        max_items = min(self.policy.max_items, requested)
        result = await self._loader(
            user_id=self.user_id,
            query=operation.query or "",
            token_budget=self.policy.token_budget,
            min_confidence=self.policy.min_confidence,
            max_items=max_items,
            candidate_limit=self.policy.candidate_limit,
            excluded_source_message_id=self.policy.excluded_source_message_id,
        )
        items = [self._to_search_item(block.content, block.source) for block in result.blocks]
        return items[operation.offset : operation.offset + operation.limit]

    def _to_search_item(self, content: str, source: str | None) -> SearchItem:
        now = datetime.now(UTC)
        observed_at = now
        score: float | None = None
        try:
            payload = json.loads(content)
            raw_observed_at = payload.get("observed_at")
            if isinstance(raw_observed_at, str):
                observed_at = datetime.fromisoformat(raw_observed_at)
            raw_score = payload.get("relevance_score")
            if isinstance(raw_score, int | float):
                score = float(raw_score)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        key = source or f"memory:{abs(hash(content))}"
        return SearchItem(
            namespace=self.namespace,
            key=key,
            value={"content": content, "source": source},
            created_at=observed_at,
            updated_at=observed_at,
            score=score,
        )
