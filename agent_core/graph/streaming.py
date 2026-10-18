from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langgraph.config import get_stream_writer

from agent_core.graph.runtime import AgentLoopEvent, AgentStreamItem
from agent_core.llm.types import LLMChunk

AgentGraphStreamChannel = Literal["agent_status", "model_chunk"]


@dataclass(frozen=True, slots=True)
class AgentGraphStreamEvent:
    """Typed custom event transported by LangGraph's native stream channel."""

    channel: AgentGraphStreamChannel
    item: AgentStreamItem


def write_agent_stream_item(item: AgentStreamItem) -> None:
    channel: AgentGraphStreamChannel = (
        "agent_status" if isinstance(item, AgentLoopEvent) else "model_chunk"
    )
    get_stream_writer()(AgentGraphStreamEvent(channel=channel, item=item))


def map_graph_stream_event(event: object) -> AgentStreamItem:
    if not isinstance(event, AgentGraphStreamEvent):
        raise TypeError("LangGraph custom stream event không đúng định dạng")
    if event.channel == "agent_status" and not isinstance(event.item, AgentLoopEvent):
        raise TypeError("LangGraph agent_status event phải chứa AgentLoopEvent")
    if event.channel == "model_chunk" and not isinstance(event.item, LLMChunk):
        raise TypeError("LangGraph model_chunk event phải chứa LLMChunk")
    return event.item
