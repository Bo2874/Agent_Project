from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from agent_core.graph import AgentGraphContext, build_agent_graph, create_initial_agent_state


async def test_graph_interrupts_and_resumes_before_tool_execution() -> None:
    model_calls = 0

    async def prepare_context(state: dict[str, Any], store: object | None) -> dict[str, Any]:
        assert store is None
        return {"prepared_messages": state["messages"]}

    async def call_model(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return {
                "model_content": "",
                "pending_tool_calls": [
                    {
                        "id": "call_approval",
                        "name": "sensitive_tool",
                        "arguments": {"value": 1},
                        "raw_arguments": '{"value":1}',
                        "parse_error_content": None,
                        "parse_error_code": None,
                    }
                ],
            }
        return {
            "model_content": "Tool completed.",
            "pending_tool_calls": [],
            "finish_reason": "stop",
        }

    async def request_approvals(_: dict[str, Any]) -> dict[str, Any]:
        approved = interrupt(
            {
                "type": "tool_approval",
                "step_number": 1,
                "tool_call": {
                    "id": "call_approval",
                    "name": "sensitive_tool",
                    "arguments": {"value": 1},
                    "raw_arguments": '{"value":1}',
                },
            }
        )
        return {"tool_approvals": {"call_approval": approved is True}}

    async def execute_tools(state: dict[str, Any]) -> dict[str, Any]:
        assert state["tool_approvals"] == {"call_approval": True}
        return {"pending_tool_calls": [], "step_number": 2}

    async def build_final(state: dict[str, Any]) -> dict[str, Any]:
        return {"final_answer": state["model_content"]}

    graph = build_agent_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "approval-test"}}
    dependencies = AgentGraphContext(
        prepare_context=prepare_context,
        call_model=call_model,
        request_approvals=request_approvals,
        execute_tools=execute_tools,
        build_final=build_final,
    )
    first_updates = [
        update
        async for update in graph.astream(
            create_initial_agent_state(
                [HumanMessage(content="Use sensitive tool")],
                goal="Complete task",
            ),
            config=config,
            context=dependencies,
            stream_mode="updates",
        )
    ]

    interrupt_update = next(update for update in first_updates if "__interrupt__" in update)
    approval_interrupt = interrupt_update["__interrupt__"][0]
    assert approval_interrupt.value["type"] == "tool_approval"
    assert approval_interrupt.value["tool_call"]["name"] == "sensitive_tool"

    result = await graph.ainvoke(
        Command(resume=True),
        config=config,
        context=dependencies,
    )

    assert result["phase"] == "completed"
    assert result["tool_approvals"] == {"call_approval": True}
    assert result["node_trace"] == [
        "context",
        "model",
        "approval",
        "tool",
        "context",
        "model",
        "final",
    ]
