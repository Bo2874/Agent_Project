"""The first answer chunk must arrive while the model is still generating."""

import asyncio

import pytest
from test_agent_graph_runtime import FakeModel, make_context, make_registry

from agent_core.graph import AgentLimits, stream_agent_graph
from agent_core.llm.types import LLMChunk, LLMToolCallDelta


@pytest.mark.parametrize("after_tool", [False, True])
async def test_react_forwards_answer_chunks_before_model_finishes(after_tool):
    release_model, first_visible = asyncio.Event(), asyncio.Event()

    class GatedModel(FakeModel):
        async def stream(self, messages, tools=None, user_id=None):
            if after_tool and not self.requests:
                self.requests.append((messages, tools))
                yield LLMChunk(
                    tool_call_deltas=(
                        LLMToolCallDelta(0, "echo", "echo_text", '{"text":"evidence"}'),
                    ),
                    finish_reason="tool_calls",
                )
                return
            self.requests.append((messages, tools))
            yield LLMChunk(content="Đã ")
            await release_model.wait()
            yield LLMChunk(content="hoàn thành.", finish_reason="stop")

    chunks = []

    async def consume():
        async for item in stream_agent_graph(
            model=GatedModel([]),
            messages=[{"role": "user", "content": "Create or edit my file"}],
            registry=make_registry(),
            context=make_context(),
            react_enabled=True,
        ):
            if isinstance(item, LLMChunk) and item.content:
                chunks.append(item.content)
                first_visible.set()

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(first_visible.wait(), timeout=2)
        assert chunks == ["Đã "]
        assert not task.done(), "Model is deliberately blocked until the first chunk is visible"
    finally:
        release_model.set()
        await task
    assert chunks == ["Đã ", "hoàn thành."]


async def test_timeout_replaces_streamed_prefix_with_the_host_partial_answer():
    class TimedOutModel(FakeModel):
        async def stream(self, *args, **kwargs):
            yield LLMChunk(content="UNFINISHED_MODEL_PREFIX")
            await asyncio.Event().wait()

    items = [
        item
        async for item in stream_agent_graph(
            model=TimedOutModel([]),
            messages=[{"role": "user", "content": "Create a file"}],
            registry=make_registry(),
            context=make_context(),
            react_enabled=True,
            limits=AgentLimits(max_duration_seconds=1),
        )
    ]
    chunks = [item for item in items if isinstance(item, LLMChunk)]
    assert chunks[0].content == "UNFINISHED_MODEL_PREFIX"
    visible = ""
    for chunk in chunks:
        if chunk.reset_content:
            visible = ""
        visible += chunk.content
    assert "UNFINISHED_MODEL_PREFIX" not in visible
    assert "Yêu cầu chưa hoàn tất" in visible
