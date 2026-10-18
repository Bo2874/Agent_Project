from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent_core.graph import AgentLoopEvent, stream_agent_graph
from agent_core.graph.checkpointing import (
    agent_checkpoint_config,
    agent_thread_id,
)
from agent_core.llm.types import LLMChunk, ModelMessage, TokenUsage
from agent_core.tools import ToolContext, ToolRegistry


class FailingModel:
    provider = "fake"
    model = "failing-model"

    async def stream(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, object]] | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        del messages, tools, user_id
        raise RuntimeError("temporary model failure")
        yield  # pragma: no cover

    async def aclose(self) -> None:
        return None


class FinalModel:
    provider = "fake"
    model = "final-model"

    async def stream(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, object]] | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        del messages, tools, user_id
        yield LLMChunk(content="resumed")
        yield LLMChunk(
            usage=TokenUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
            finish_reason="stop",
        )

    async def aclose(self) -> None:
        return None


def test_agent_checkpoint_config_is_stable_per_run() -> None:
    run_id = uuid4()

    config = agent_checkpoint_config(run_id, recursion_limit=41)

    assert agent_thread_id(run_id) == f"agent-run:{run_id}"
    assert config == {
        "configurable": {
            "thread_id": f"agent-run:{run_id}",
        },
        "recursion_limit": 41,
    }


@pytest.mark.asyncio
async def test_external_checkpointer_resumes_same_run_without_deleting_thread() -> None:
    run_id = uuid4()
    context = ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_role="user",
        run_id=run_id,
    )
    saver = InMemorySaver()
    with pytest.raises(RuntimeError):
        _ = [
            item
            async for item in stream_agent_graph(
                model=FailingModel(),
                messages=[{"role": "user", "content": "continue after failure"}],
                registry=ToolRegistry(),
                context=context,
                graph_checkpointer=saver,
            )
        ]

    config = agent_checkpoint_config(run_id)
    assert await saver.aget_tuple(config) is not None

    events: list[AgentLoopEvent] = []

    async def capture(event: AgentLoopEvent) -> None:
        events.append(event)

    items = [
        item
        async for item in stream_agent_graph(
            model=FinalModel(),
            messages=[],
            registry=ToolRegistry(),
            context=context,
            event_handler=capture,
            resume=True,
            graph_checkpointer=saver,
        )
    ]

    assert any(isinstance(item, LLMChunk) and item.content == "resumed" for item in items)
    assert events[0].kind == "model_started"
    assert await saver.aget_tuple(config) is not None

    await saver.adelete_thread(agent_thread_id(run_id))
