from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from agent_core.graph import (
    AgentGraphContext,
    build_agent_graph,
    create_agent_budget,
    create_initial_agent_state,
    limit_reason,
    model_node,
    route_after_context,
    route_after_model,
    route_after_tool,
)


def test_initial_agent_state_is_complete_and_uses_langchain_messages() -> None:
    message = HumanMessage(content="Xin chào")

    state = create_initial_agent_state([message], goal="  Trả lời user  ")

    assert state["messages"] == [message]
    assert state["goal"] == "Trả lời user"
    assert state["phase"] == "created"
    assert state["step_number"] == 1
    assert state["usage"]["total_tokens"] == 0
    assert state["pending_tool_calls"] == []
    assert state["budget"]["max_steps"] == 8
    assert state["budget"]["max_total_tokens"] == 20_000
    assert state["node_trace"] == []


async def test_graph_routes_direct_model_answer_to_final() -> None:
    calls: list[str] = []

    async def prepare_context(state: dict[str, Any], store: object | None) -> dict[str, Any]:
        assert store is None
        calls.append("context")
        assert state["phase"] == "created"
        return {
            "prepared_messages": state["messages"],
            "loaded_tool_names": ["echo_text"],
            "progress": ["Context đã sẵn sàng."],
        }

    async def call_model(state: dict[str, Any]) -> dict[str, Any]:
        calls.append("model")
        assert state["phase"] == "context_ready"
        return {
            "model_content": "Xin chào từ LangGraph.",
            "finish_reason": "stop",
        }

    async def execute_tools(_: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("Direct answer must not enter the tool node")

    async def build_final(state: dict[str, Any]) -> dict[str, Any]:
        calls.append("final")
        assert state["phase"] == "model_completed"
        return {"final_answer": state["model_content"]}

    dependencies = AgentGraphContext(
        prepare_context=prepare_context,
        call_model=call_model,
        execute_tools=execute_tools,
        build_final=build_final,
    )
    graph = build_agent_graph()
    initial = create_initial_agent_state(
        [HumanMessage(content="Xin chào")],
        goal="Trả lời user",
    )

    result = await graph.ainvoke(initial, context=dependencies)

    assert calls == ["context", "model", "final"]
    assert result["phase"] == "completed"
    assert result["node_trace"] == ["context", "model", "final"]
    assert result["final_answer"] == "Xin chào từ LangGraph."
    assert result["loaded_tool_names"] == ["echo_text"]
    assert result["progress"] == ["Context đã sẵn sàng."]


async def test_graph_routes_model_to_tool_and_back_to_model() -> None:
    calls: list[str] = []
    model_calls = 0

    async def prepare_context(state: dict[str, Any], store: object | None) -> dict[str, Any]:
        assert store is None
        calls.append("context")
        return {"prepared_messages": state["messages"]}

    async def call_model(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal model_calls
        model_calls += 1
        calls.append("model")
        if model_calls == 1:
            return {
                "model_content": "",
                "pending_tool_calls": [
                    {
                        "id": "call_1",
                        "name": "echo_text",
                        "arguments": {"text": "hello"},
                        "raw_arguments": '{"text":"hello"}',
                        "parse_error_content": None,
                        "parse_error_code": None,
                    }
                ],
            }
        return {
            "model_content": "Tool returned hello.",
            "pending_tool_calls": [],
            "finish_reason": "stop",
        }

    async def execute_tools(_: dict[str, Any]) -> dict[str, Any]:
        calls.append("tool")
        return {"pending_tool_calls": [], "step_number": 2}

    async def request_approvals(_: dict[str, Any]) -> dict[str, Any]:
        calls.append("approval")
        return {"tool_approvals": {}}

    async def build_final(state: dict[str, Any]) -> dict[str, Any]:
        calls.append("final")
        return {"final_answer": state["model_content"]}

    graph = build_agent_graph()
    result = await graph.ainvoke(
        create_initial_agent_state([HumanMessage(content="Use a tool")], goal="Reply"),
        context=AgentGraphContext(
            prepare_context=prepare_context,
            call_model=call_model,
            execute_tools=execute_tools,
            build_final=build_final,
            request_approvals=request_approvals,
        ),
    )

    assert calls == [
        "context",
        "model",
        "approval",
        "tool",
        "context",
        "model",
        "final",
    ]
    assert result["node_trace"] == calls
    assert result["final_answer"] == "Tool returned hello."
    assert result["step_number"] == 2


def test_graph_contains_six_named_nodes_and_conditional_router() -> None:
    graph = build_agent_graph().get_graph()

    assert {"context", "model", "approval", "tool", "final", "limit"}.issubset(graph.nodes)

    state = create_initial_agent_state([HumanMessage(content="Hi")], goal="Reply")
    assert route_after_model(state) == "final"
    state["pending_tool_calls"] = [
        {
            "id": "call_1",
            "name": "echo_text",
            "arguments": {},
            "raw_arguments": "{}",
            "parse_error_content": None,
            "parse_error_code": None,
        }
    ]
    assert route_after_model(state) == "approval"


def test_graph_routers_enforce_budget_and_loop_limits_from_state() -> None:
    state = create_initial_agent_state(
        [HumanMessage(content="Use a tool")],
        goal="Reply",
        budget=create_agent_budget(
            max_steps=1,
            max_total_tokens=10,
            max_duration_seconds=5,
            max_identical_tool_calls=2,
            max_repeated_observations=2,
        ),
    )
    state["phase"] = "context_ready"
    state["step_number"] = 2
    assert limit_reason(state) == "step_limit"
    assert route_after_context(state) == "limit"

    state["step_number"] = 1
    state["phase"] = "model_completed"
    state["pending_tool_calls"] = [
        {
            "id": "call_1",
            "name": "echo_text",
            "arguments": {},
            "raw_arguments": "{}",
            "parse_error_content": None,
            "parse_error_code": None,
        }
    ]
    assert limit_reason(state) == "step_limit"
    assert route_after_model(state) == "limit"

    state["budget"]["max_steps"] = 2
    state["usage"]["total_tokens"] = 10
    assert limit_reason(state) == "token_limit"

    state["usage"]["total_tokens"] = 0
    state["budget"]["elapsed_seconds"] = 5
    assert limit_reason(state) == "time_limit"

    state["budget"]["elapsed_seconds"] = 0
    state["tool_call_counts"] = {"same-call": 3}
    assert limit_reason(state) == "repeated_tool_call"

    state["phase"] = "tools_completed"
    state["tool_call_counts"] = {}
    state["observation_counts"] = {"same-result": 3}
    assert limit_reason(state) == "repeated_observation"
    assert route_after_tool(state) == "limit"


async def test_node_handler_cannot_override_graph_lifecycle() -> None:
    async def invalid(_: dict[str, Any]) -> dict[str, Any]:
        return {"model_content": "answer", "phase": "completed"}

    async def unused(_: dict[str, Any]) -> dict[str, Any]:
        return {}

    dependencies = AgentGraphContext(
        prepare_context=unused,
        call_model=invalid,
        execute_tools=unused,
        build_final=unused,
    )
    state = create_initial_agent_state([HumanMessage(content="Hi")], goal="Reply")
    state["phase"] = "context_ready"

    with pytest.raises(ValueError, match="lifecycle key"):
        await model_node(state, Runtime(context=dependencies))


@pytest.mark.parametrize(
    ("messages", "goal", "error"),
    [
        ([], "goal", "ít nhất một message"),
        ([HumanMessage(content="Hi")], "   ", "goal không được để trống"),
        ([{"role": "user", "content": "Hi"}], "goal", "BaseMessage"),
    ],
)
def test_initial_agent_state_rejects_invalid_input(
    messages: list[Any],
    goal: str,
    error: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        create_initial_agent_state(messages, goal=goal)  # type: ignore[arg-type]
