from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import RetryPolicy, TimeoutPolicy

from agent_core.graph.limits import (
    limit_node,
    route_after_context,
    route_after_model,
    route_after_tool,
)
from agent_core.graph.nodes import (
    AgentGraphContext,
    approval_node,
    context_node,
    final_node,
    model_node,
    node_error_handler,
    tool_node,
)
from agent_core.graph.state import AgentState
from agent_core.llm.deepseek import LLMProviderError

MODEL_RETRY_POLICY = RetryPolicy(
    initial_interval=0.2,
    backoff_factor=2,
    max_interval=1,
    max_attempts=2,
    jitter=False,
    retry_on=LLMProviderError,
)


def build_agent_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    store: BaseStore | None = None,
    node_timeout_seconds: float | None = None,
) -> CompiledStateGraph:
    """Compile the production model -> tool -> model conditional workflow."""
    builder = StateGraph(AgentState, context_schema=AgentGraphContext)
    timeout = (
        TimeoutPolicy(run_timeout=node_timeout_seconds)
        if node_timeout_seconds is not None
        else None
    )
    builder.add_node(
        "context",
        context_node,
        error_handler=node_error_handler,
        timeout=timeout,
    )
    builder.add_node(
        "model",
        model_node,
        retry_policy=MODEL_RETRY_POLICY,
        error_handler=node_error_handler,
        timeout=timeout,
    )
    builder.add_node("approval", approval_node, error_handler=node_error_handler)
    builder.add_node(
        "tool",
        tool_node,
        error_handler=node_error_handler,
        timeout=timeout,
    )
    builder.add_node(
        "final",
        final_node,
        error_handler=node_error_handler,
        timeout=timeout,
    )
    builder.add_node("limit", limit_node)
    builder.add_edge(START, "context")
    builder.add_conditional_edges(
        "context",
        route_after_context,
        {"model": "model", "limit": "limit"},
    )
    builder.add_conditional_edges(
        "model",
        route_after_model,
        {"approval": "approval", "final": "final", "limit": "limit"},
    )
    builder.add_edge("approval", "tool")
    builder.add_conditional_edges(
        "tool",
        route_after_tool,
        {"context": "context", "final": "final", "limit": "limit"},
    )
    builder.add_edge("final", END)
    builder.add_edge("limit", END)
    return builder.compile(checkpointer=checkpointer, store=store, name="personal-agent")
