import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage

from agent_core.graph import (
    AgentGraphContext,
    build_agent_graph,
    create_initial_agent_state,
    stream_agent_graph,
)
from agent_core.llm.deepseek import LLMProviderError
from agent_core.llm.types import LLMChunk, ModelMessage
from agent_core.tools import ToolContext, ToolRegistry


async def _prepare_context(
    state: dict[str, Any],
    _store: object | None,
) -> dict[str, Any]:
    return {"prepared_messages": state["messages"]}


async def _unused(_: dict[str, Any]) -> dict[str, Any]:
    return {}


async def _build_final(state: dict[str, Any]) -> dict[str, Any]:
    return {"final_answer": state["model_content"]}


def _context(call_model: Any) -> AgentGraphContext:
    return AgentGraphContext(
        prepare_context=_prepare_context,
        call_model=call_model,
        execute_tools=_unused,
        build_final=_build_final,
    )


def _initial_state() -> dict[str, Any]:
    return create_initial_agent_state(
        [HumanMessage(content="Xin chào")],
        goal="Trả lời user",
    )


async def test_langgraph_retries_transient_model_node_once() -> None:
    attempts = 0

    async def flaky_model(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LLMProviderError("temporary provider failure")
        return {
            "model_content": "Đã phục hồi.",
            "pending_tool_calls": [],
            "finish_reason": "stop",
        }

    result = await build_agent_graph().ainvoke(
        _initial_state(),
        context=_context(flaky_model),
    )

    assert attempts == 2
    assert result["phase"] == "completed"
    assert result["final_answer"] == "Đã phục hồi."


async def test_langgraph_does_not_retry_logic_error_and_checkpoints_failure() -> None:
    attempts = 0

    async def invalid_model(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid model payload")

    result = await build_agent_graph().ainvoke(
        _initial_state(),
        context=_context(invalid_model),
    )

    assert attempts == 1
    assert result["phase"] == "failed"
    assert result["error_code"] == "agent_node_error"
    assert result["error_node"] == "model"
    assert result["node_trace"] == ["context", "model"]


async def test_langgraph_enforces_async_node_timeout() -> None:
    async def slow_model(_: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.2)
        return {"model_content": "Quá muộn", "pending_tool_calls": []}

    result = await build_agent_graph(node_timeout_seconds=0.05).ainvoke(
        _initial_state(),
        context=_context(slow_model),
    )

    assert result["phase"] == "failed"
    assert result["error_code"] == "agent_time_limit"
    assert result["error_node"] == "model"


async def test_external_task_cancel_propagates_through_langgraph() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def cancellable_model(_: dict[str, Any]) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
        return {"model_content": "Không chạy tới đây", "pending_tool_calls": []}

    task = asyncio.create_task(
        build_agent_graph().ainvoke(
            _initial_state(),
            context=_context(cancellable_model),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_production_stream_retries_before_first_chunk_without_duplicate_delta() -> None:
    class FlakyStreamingModel:
        provider = "fake"
        model = "fake-retry-model"

        def __init__(self) -> None:
            self.attempts = 0

        async def stream(
            self,
            _messages: list[ModelMessage],
            tools: list[dict[str, Any]] | None = None,
            user_id: str | None = None,
        ) -> AsyncIterator[LLMChunk]:
            del tools
            assert user_id is not None
            self.attempts += 1
            if self.attempts == 1:
                raise LLMProviderError("temporary provider failure")
            yield LLMChunk(content="Một lần duy nhất.", finish_reason="stop")

        async def aclose(self) -> None:
            return None

    model = FlakyStreamingModel()
    context = ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_role="user",
    )

    items = [
        item
        async for item in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Xin chào"}],
            registry=ToolRegistry(),
            context=context,
        )
    ]

    assert model.attempts == 2
    assert "".join(item.content for item in items if isinstance(item, LLMChunk)) == (
        "Một lần duy nhất."
    )
