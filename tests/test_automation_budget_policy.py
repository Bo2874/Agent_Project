"""Nullable aggregate limits must not change context limits or replay safety."""

from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_budgeting import Model, actions, run, text
from test_task_workers import Model as WorkerModel
from test_task_workers import proposal, registry

from agent_core.contracts import TaskBudget
from agent_core.graph import AgentLimits, AgentLoopEvent, AgentQuotaLimitError, stream_agent_graph
from agent_core.graph.budgeting import unreserved_usage
from agent_core.llm.types import LLMChunk, TokenUsage
from agent_core.task_workers import SharedWorkerBudget
from agent_core.tools import ToolContext, ToolResult


async def test_unbounded_root_and_children_can_exceed_old_cap():
    async def read(context, args):
        return ToolResult.success("verified", usage=TokenUsage(total_tokens=25_000))

    model = WorkerModel([proposal("alpha"), proposal("beta")])
    items = [
        i
        async for i in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Research"}],
            registry=registry(read),
            context=ToolContext(uuid4(), uuid4(), "user"),
            limits=AgentLimits(max_total_tokens=None),
            react_enabled=True,
            planning_enabled=True,
            parallel_enabled=True,
        )
    ]
    final = [i for i in items if isinstance(i, LLMChunk) and i.usage][-1]
    assert text(items) == "Workers complete"
    assert final.usage.total_tokens == 50_150
    snapshots = [
        i.planning_snapshot for i in items if isinstance(i, AgentLoopEvent) and i.planning_sync
    ]
    assert snapshots[-1]["statuses"] == {"alpha": "succeeded", "beta": "succeeded"}
    assert all(t["budget"]["max_tokens"] is None for t in snapshots[-1]["spec"]["tasks"])


async def test_null_budget_checkpoint_resume():
    saver = InMemorySaver()
    context = ToolContext(uuid4(), uuid4(), "user", run_id=uuid4())
    model = Model(
        [[LLMChunk(content="done", finish_reason="stop", usage=TokenUsage(total_tokens=30_000))]]
    )
    kwargs = dict(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        registry=registry(lambda *a: None),
        context=context,
        react_enabled=True,
        limits=AgentLimits(max_total_tokens=None),
        graph_checkpointer=saver,
    )
    # Use the same durable identity; a completed checkpoint returns without a second model call.
    first = [i async for i in stream_agent_graph(**kwargs)]
    kwargs["limits"] = AgentLimits(max_total_tokens=100)
    second = [i async for i in stream_agent_graph(**kwargs, resume=True)]
    assert text(first) == text(second) == "done"
    assert model.calls == 1
    assert (
        TaskBudget.model_validate_json(
            TaskBudget(max_tokens=None, max_steps=None, max_tool_calls=None).model_dump_json()
        ).max_tokens
        is None
    )


async def test_existing_chat_and_finite_checkpoint_unchanged():
    async def handler(*args):
        return ToolResult.success("paid", usage=TokenUsage(total_tokens=25_000))

    model = Model([actions("search_web")])
    items = await run(model, handler=handler)
    assert AgentLimits().max_total_tokens == 20_000
    assert "chưa hoàn tất" in text(items)
    assert model.calls == 1


async def test_unknown_billing_still_blocks_replay():
    pool = SharedWorkerBudget(None)
    async with pool.reserve(100, unreserved_usage):
        pass  # Provider never reported actual usage.
    assert pool.held == 100
    with pytest.raises(AgentQuotaLimitError):
        async with pool.reserve(100, unreserved_usage):
            pytest.fail("Unknown spend must prevent another admission")


async def test_unbounded_reservations_keep_actual_overruns():
    pool = SharedWorkerBudget(None)
    for _ in range(2):
        async with pool.reserve(100, unreserved_usage) as receipt:
            receipt.actual = 40_000
    assert pool.spent == 80_000 and not pool.stopped


def test_execution_policy_changes_only_automation_aggregate():
    from backend.core.config import Settings
    from backend.services.automations.execution import execution_limits_for_source

    settings = Settings(_env_file=None, db_password="fixture", auth_secret_key="x" * 32)
    assert (
        execution_limits_for_source("chat", settings).max_total_tokens
        == settings.agent_run_token_budget
    )
    auto = execution_limits_for_source("automation", settings)
    assert auto.max_total_tokens is auto.max_steps is auto.max_duration_seconds is None
    with pytest.raises(ValueError):
        execution_limits_for_source("untrusted", settings)
