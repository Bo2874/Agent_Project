import json
from copy import deepcopy
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent_core import planning as dag
from agent_core.graph import AgentLimits, AgentLoopEvent, stream_agent_graph
from agent_core.llm.deepseek import LLMProviderError
from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
from agent_core.tools import (
    ToolContext,
    ToolExecutionPolicy,
    ToolRegistry,
    ToolResult,
    create_context_tool,
)


def task(key, tools=(), deps=(), **kwargs):
    return {
        "task_id": key,
        "goal": "Complete " + key,
        "tools": list(tools),
        "depends_on": list(deps),
        "acceptance_criteria": ["Use confirmed observations"],
        **kwargs,
    }


def action(name, args, index=0):
    return LLMToolCallDelta(
        index=index, id=f"call_{name}_{index}", name=name, arguments=json.dumps(args)
    )


def response(*calls):
    return [
        LLMChunk(
            tool_call_deltas=calls, finish_reason="tool_calls", usage=TokenUsage(total_tokens=100)
        )
    ]


def finish(status="succeeded", summary="Verified result"):
    return response(action("finish_task", {"status": status, "summary": summary}))


class Model:
    provider, model, max_tokens = "test", "planning-fixture", 100

    def __init__(self, replies):
        self.replies, self.requests = replies, []

    async def stream(self, messages, **kwargs):
        self.requests.append((deepcopy(messages), kwargs.get("tools")))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        for chunk in reply:
            yield chunk


def registry(log, *, fail_alpha=False, approval=False):
    result = ToolRegistry()
    for name in ("alpha", "beta"):

        async def execute(context, args, name=name):
            log.append(name)
            if name == "alpha" and fail_alpha:
                return ToolResult.failure("Unavailable", error_code="fixture_failure")
            return ToolResult.success("7" if name == "alpha" else "119")

        result.register(
            create_context_tool(
                name=name,
                description="Deterministic read fixture",
                args_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                coroutine=execute,
            ),
            policy=ToolExecutionPolicy(requires_approval=approval),
        )
    return result


def context():
    return ToolContext(user_id=uuid4(), conversation_id=uuid4(), run_id=uuid4(), user_role="user")


async def execute(model, tools, **kwargs):
    return [
        i
        async for i in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Create a plan, then use alpha and beta"}],
            registry=tools,
            context=kwargs.pop("context", context()),
            react_enabled=True,
            planning_enabled=True,
            limits=AgentLimits(max_total_tokens=80_000, max_steps=12),
            **kwargs,
        )
    ]


def snapshots(items):
    return [
        i.planning_snapshot
        for i in items
        if isinstance(i, AgentLoopEvent) and i.planning_sync and i.planning_snapshot
    ]


@pytest.mark.parametrize(
    "tasks",
    [
        [task("a", deps=("b",)), task("b", deps=("a",))],
        [task("a", deps=("missing",))],
        [task("a"), task("a")],
        [task("a", tools=("missing",))],
        [task("a", max_tokens=100_000)],
        [task("a", max_steps=12)],
        [task("a", timeout_seconds=999)],
        [task("a", agent_type="document_researcher", tools=("alpha",))],
        [task(f"t{i}") for i in range(9)],
    ],
)
def test_invalid_or_infeasible_plans_are_rejected_before_execution(tasks):
    log = []
    with pytest.raises(ValueError):
        dag.create_plan(
            {"tasks": tasks},
            goal="User request",
            registry=registry(log),
            role="user",
            remaining_tokens=20_000,
            remaining_steps=6,
            remaining_seconds=120,
        )
    assert not log


async def test_tasks_run_in_dependency_order_with_scoped_tools_and_shared_usage():
    log = []
    model = Model(
        [
            response(
                action(
                    "create_plan", {"tasks": [task("a", ("alpha",)), task("b", ("beta",), ("a",))]}
                )
            ),
            response(action("alpha", {"query": "count"})),
            finish(summary="alpha returned 7"),
            response(action("beta", {"query": "7 times 17"})),
            finish(summary="beta returned 119"),
            [LLMChunk(content="119", finish_reason="stop", usage=TokenUsage(total_tokens=100))],
        ]
    )
    items = await execute(model, registry(log))
    assert log == ["alpha", "beta"]
    first = snapshots(items)[0]
    final = snapshots(items)[-1]
    assert first["statuses"] == {"a": "pending", "b": "pending"}
    assert final["statuses"] == {"a": "succeeded", "b": "succeeded"}
    assert sum(r["usage"]["total_tokens"] for r in final["results"].values()) == 400
    assert [i.usage.total_tokens for i in items if isinstance(i, LLMChunk) and i.usage][-1] == 600
    assert {d["function"]["name"] for d in model.requests[1][1]} == {"alpha", "finish_task"}
    assert {d["function"]["name"] for d in model.requests[3][1]} == {"beta", "finish_task"}
    assert "alpha returned 7" in json.dumps(model.requests[3][0])
    assert not model.requests[-1][1]


async def test_failed_dependency_blocks_branch_but_independent_task_continues():
    log = []
    model = Model(
        [
            response(
                action(
                    "create_plan",
                    {"tasks": [task("a", ("alpha",)), task("b", ("beta",), ("a",)), task("c")]},
                )
            ),
            response(action("alpha", {"query": "fail"})),
            finish("failed", "No result"),
            finish(summary="Independent task complete"),
            [LLMChunk(content="Partial: dependency failed", finish_reason="stop")],
        ]
    )
    items = await execute(model, registry(log, fail_alpha=True))
    assert log == ["alpha"]
    assert snapshots(items)[-1]["statuses"] == {"a": "failed", "b": "blocked", "c": "succeeded"}
    assert any(
        isinstance(i, AgentLoopEvent)
        and i.progress
        and i.progress.get("stop_reason") == "task_failed"
        for i in items
    )


async def test_mixed_control_and_real_tool_batch_does_not_execute_or_ask_approval():
    async def approval(*args):
        pytest.fail("Must not request approval for rejected mixed batch")

    log = []
    model = Model(
        [
            response(
                action("create_plan", {"tasks": [task("a", ("alpha",))]}),
                action("alpha", {"query": "bad"}, 1),
            ),
            [LLMChunk(content="No action performed", finish_reason="stop")],
        ]
    )
    items = await execute(model, registry(log, approval=True), approval_handler=approval)
    assert not log and not snapshots(items)


async def test_task_cannot_use_a_sibling_tool_or_approve_it():
    async def approval(*args):
        pytest.fail("Out-of-task tool must not reach approval")

    log = []
    model = Model(
        [
            response(action("create_plan", {"tasks": [task("a", ("alpha",))]})),
            response(action("beta", {"query": "unauthorized"})),
            finish("failed"),
            [LLMChunk(content="Not executed", finish_reason="stop")],
        ]
    )
    items = await execute(model, registry(log, approval=True), approval_handler=approval)
    assert not log and snapshots(items)[-1]["statuses"]["a"] == "failed"


async def test_success_without_required_observation_is_rejected():
    log = []
    model = Model(
        [
            response(action("create_plan", {"tasks": [task("a", ("alpha",))]})),
            finish(),
            [LLMChunk(content="Missing evidence", finish_reason="stop")],
        ]
    )
    items = await execute(model, registry(log))
    assert (
        not log
        and snapshots(items)[-1]["results"]["a"]["error"] == "missing_successful_observation"
    )


async def test_task_budget_prevents_paid_model_dispatch_and_has_valid_tool_protocol():
    log = []
    model = Model(
        [
            response(action("create_plan", {"tasks": [task("a", ("alpha",), max_tokens=128)]})),
            [LLMChunk(content="Task budget insufficient", finish_reason="stop")],
        ]
    )
    items = await execute(model, registry(log))
    assert not log and len(model.requests) == 2
    assert snapshots(items)[-1]["statuses"]["a"] == "failed"
    assert "host_budget" in json.dumps(model.requests[-1][0])


async def test_resume_restores_dag_without_rerunning_completed_task():
    log, saver, ctx = [], InMemorySaver(), context()
    tools = registry(log)
    model = Model(
        [
            response(
                action(
                    "create_plan", {"tasks": [task("a", ("alpha",)), task("b", ("beta",), ("a",))]}
                )
            ),
            response(action("alpha", {"query": "a"})),
            finish(),
            LLMProviderError("Transient fixture"),
            response(action("beta", {"query": "b"})),
            finish(),
            [LLMChunk(content="Both done", finish_reason="stop")],
        ]
    )
    with pytest.raises(LLMProviderError):
        await execute(model, tools, context=ctx, graph_checkpointer=saver)
    assert log == ["alpha"]
    items = await execute(model, tools, context=ctx, graph_checkpointer=saver, resume=True)
    assert log == ["alpha", "beta"]
    assert snapshots(items)[-1]["statuses"] == {"a": "succeeded", "b": "succeeded"}


async def test_atomic_web_task_uses_validated_direct_answer_without_duplicate_model_turn():
    log = []
    tools = registry(log)

    async def search(ctx, args):
        log.append("search_web")
        return ToolResult.success(
            "Verified [source](https://example.com)",
            data={"sources": [{"url": "https://example.com"}]},
            usage=TokenUsage(total_tokens=1000),
        )

    tools.register(
        create_context_tool(
            name="search_web",
            description="Native web fixture",
            args_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            coroutine=search,
            return_direct=True,
        )
    )
    model = Model(
        [
            response(
                action(
                    "create_plan",
                    {
                        "tasks": [
                            task(
                                "web",
                                ("search_web",),
                                agent_type="web_researcher",
                                max_tool_calls=1,
                            ),
                            task("compare", deps=("web",)),
                        ]
                    },
                )
            ),
            response(action("search_web", {"query": "Official source"})),
            finish(),
            [
                LLMChunk(
                    content="Final with source",
                    usage=TokenUsage(total_tokens=100),
                    finish_reason="stop",
                )
            ],
        ]
    )
    items = await execute(model, tools)
    final = snapshots(items)[-1]
    assert final["statuses"] == {"web": "succeeded", "compare": "succeeded"}
    assert final["results"]["web"]["usage"]["total_tokens"] == 1100
    assert len(model.requests) == 4 and log == ["search_web"]
    assert [i.usage.total_tokens for i in items if isinstance(i, LLMChunk) and i.usage][-1] == 1400
