from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent_core.context_budget import ContextBlock
from agent_core.graph import stream_agent_graph
from agent_core.graph.checkpointing import agent_checkpoint_config, agent_thread_id
from agent_core.llm.types import LLMChunk, ModelMessage
from agent_core.tools import ToolContext, ToolRegistry
from backend.services.langgraph_memory_store import (
    ScopedUserMemoryStore,
    UserMemorySearchPolicy,
)
from backend.services.memory import MemoryContextResult


class FinalModel:
    provider = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.requests: list[list[ModelMessage]] = []

    async def stream(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        del tools, user_id
        self.requests.append(messages)
        yield LLMChunk(content="done", finish_reason="stop")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_store_scopes_search_to_authenticated_user_and_policy() -> None:
    user_id = uuid4()
    message_id = uuid4()
    calls: list[dict[str, object]] = []

    async def loader(**kwargs: object) -> MemoryContextResult:
        calls.append(kwargs)
        return MemoryContextResult(
            blocks=(
                ContextBlock(
                    content=(
                        '{"type":"preference","key":"response.style",'
                        '"value":"Ngắn gọn","observed_at":"2026-01-01T00:00:00+00:00",'
                        '"relevance_score":0.9}'
                    ),
                    source="user_memory:memory-1",
                ),
            ),
            candidate_count=1,
            relevant_count=1,
            selected_count=1,
            token_count=20,
        )

    store = ScopedUserMemoryStore(
        user_id=user_id,
        policy=UserMemorySearchPolicy(
            token_budget=4000,
            min_confidence=Decimal("0.600"),
            max_items=20,
            candidate_limit=100,
            excluded_source_message_id=message_id,
        ),
        loader=loader,
    )

    items = await store.asearch(store.namespace, query="Trả lời thế nào?", limit=10)

    assert len(items) == 1
    assert items[0].namespace == ("user_memories", str(user_id))
    assert items[0].value["source"] == "user_memory:memory-1"
    assert items[0].score == 0.9
    assert items[0].created_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert calls == [
        {
            "user_id": user_id,
            "query": "Trả lời thế nào?",
            "token_budget": 4000,
            "min_confidence": Decimal("0.600"),
            "max_items": 10,
            "candidate_limit": 100,
            "excluded_source_message_id": message_id,
        }
    ]


@pytest.mark.asyncio
async def test_store_rejects_cross_user_read_and_all_writes() -> None:
    user_id = uuid4()

    async def loader(**_: object) -> MemoryContextResult:
        return MemoryContextResult((), 0, 0, 0, 0)

    store = ScopedUserMemoryStore(
        user_id=user_id,
        policy=UserMemorySearchPolicy(
            token_budget=4000,
            min_confidence=Decimal("0.600"),
            max_items=20,
            candidate_limit=100,
        ),
        loader=loader,
    )

    with pytest.raises(PermissionError, match="user khác"):
        await store.asearch(("user_memories", str(uuid4())), query="private")
    with pytest.raises(PermissionError, match="chỉ cho phép tìm kiếm"):
        await store.aput(store.namespace, "memory", {"content": "overwrite"})


@pytest.mark.asyncio
async def test_agent_loads_store_memory_once_and_persists_it_in_graph_state() -> None:
    user_id = uuid4()
    run_id = uuid4()
    load_count = 0

    async def loader(**_: object) -> MemoryContextResult:
        nonlocal load_count
        load_count += 1
        return MemoryContextResult(
            blocks=(
                ContextBlock(
                    content='{"key":"profile.city","value":"Đà Nẵng"}',
                    source="user_memory:city",
                ),
            ),
            candidate_count=1,
            relevant_count=1,
            selected_count=1,
            token_count=20,
        )

    store = ScopedUserMemoryStore(
        user_id=user_id,
        policy=UserMemorySearchPolicy(
            token_budget=4000,
            min_confidence=Decimal("0.600"),
            max_items=20,
            candidate_limit=100,
        ),
        loader=loader,
    )
    saver = InMemorySaver()
    model = FinalModel()
    context = ToolContext(
        user_id=user_id,
        conversation_id=uuid4(),
        user_role="user",
        run_id=run_id,
    )

    _ = [
        item
        async for item in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Tôi sống ở đâu?"}],
            registry=ToolRegistry(),
            context=context,
            graph_checkpointer=saver,
            memory_store=store,
        )
    ]

    rendered = "\n".join(str(message.get("content", "")) for message in model.requests[0])
    checkpoint = await saver.aget_tuple(agent_checkpoint_config(run_id))
    assert checkpoint is not None
    state = checkpoint.checkpoint["channel_values"]
    assert load_count == 1
    assert "Đà Nẵng" in rendered
    assert state["memory_loaded"] is True
    assert state["memory_context"] == [
        {
            "content": '{"key":"profile.city","value":"Đà Nẵng"}',
            "source": "user_memory:city",
        }
    ]

    await saver.adelete_thread(agent_thread_id(run_id))
