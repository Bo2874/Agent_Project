from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from agent_core.graph import AgentLoopEvent
from agent_core.graph.streaming import (
    AgentGraphStreamEvent,
    map_graph_stream_event,
    write_agent_stream_item,
)
from agent_core.llm.types import LLMChunk


class StreamTestState(TypedDict):
    completed: bool


async def test_langgraph_custom_stream_carries_status_and_model_chunks() -> None:
    async def emit_events(_: StreamTestState) -> StreamTestState:
        write_agent_stream_item(AgentLoopEvent(kind="model_started", step_number=1))
        write_agent_stream_item(LLMChunk(content="Xin chào"))
        return {"completed": True}

    builder = StateGraph(StreamTestState)
    builder.add_node("emit", emit_events)
    builder.add_edge(START, "emit")
    builder.add_edge("emit", END)
    graph = builder.compile()

    streamed = [
        map_graph_stream_event(event)
        async for event in graph.astream(
            {"completed": False},
            stream_mode="custom",
        )
    ]

    assert isinstance(streamed[0], AgentLoopEvent)
    assert streamed[0].kind == "model_started"
    assert isinstance(streamed[1], LLMChunk)
    assert streamed[1].content == "Xin chào"


def test_graph_stream_mapper_rejects_channel_payload_mismatch() -> None:
    with pytest.raises(TypeError, match="AgentLoopEvent"):
        map_graph_stream_event(
            AgentGraphStreamEvent(
                channel="agent_status",
                item=LLMChunk(content="invalid"),
            )
        )
