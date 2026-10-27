import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_core import planning as dag
from agent_core.contracts import CapabilityDescriptor
from agent_core.graph import AgentLimits, AgentLoopEvent, stream_agent_graph
from agent_core.graph.budgeting import unreserved_usage
from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
from agent_core.task_workers import SharedWorkerBudget, WorkerCapacity, merge_result, ready_wave
from agent_core.tools import ToolContext, ToolRegistry, ToolResult, create_context_tool
from backend.services import usage_limits
from backend.services.usage_limits import AgentQuotaGuard


def proposal(key, deps=(), tools=("read_value",)):
    return {
        "task_id": key,
        "goal": "Private goal " + key,
        "tools": list(tools),
        "depends_on": list(deps),
        "acceptance_criteria": ["Use actual observations"],
    }


def call(name, arguments):
    return LLMChunk(
        tool_call_deltas=(
            LLMToolCallDelta(
                index=0, id="same_provider_id", name=name, arguments=json.dumps(arguments)
            ),
        ),
        usage=TokenUsage(total_tokens=25),
        finish_reason="tool_calls",
    )


class Model:
    provider, model, max_tokens = "fixture", "isolated-workers", 100

    def __init__(self, tasks):
        self.tasks, self.requests = tasks, []

    async def stream(self, messages, tools=None, **kwargs):
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        brief = next(
            (m["content"] for m in messages if "HOST_TASK:" in (m.get("content") or "")), None
        )
        if brief:
            data = json.loads(brief.split("\n", 1)[1])
            key = data["task"]["task_id"]
            if not any(m["role"] == "tool" for m in messages):
                yield call("read_value", {"key": key})
            else:
                yield call("finish_task", {"status": "succeeded", "summary": "Verified " + key})
        elif tools:
            yield call("create_plan", {"tasks": self.tasks})
        else:
            yield LLMChunk(
                content="Workers complete", finish_reason="stop", usage=TokenUsage(total_tokens=25)
            )


def registry(handler):
    value = ToolRegistry()
    value.register(
        create_context_tool(
            name="read_value",
            description="Read one fixture value",
            args_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            coroutine=handler,
        ),
        capability=CapabilityDescriptor(
            capability_id="read_value",
            name="read_value",
            kind="tool",
            description="Read value",
            version="1",
            side_effect="read_only",
        ),
    )
    return value


async def test_parallel_graph_isolates_messages_and_joins_dependencies():
    entered, drained = set(), set()
    both = asyncio.Event()

    async def read(context, args):
        key = args["key"]
        assert context.task_id == key
        if key in {"alpha", "beta"}:
            entered.add(key)
            if len(entered) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), timeout=3)
        else:
            assert drained == {"alpha", "beta"}
        drained.add(key)
        return ToolResult.success("Secret observation " + key)

    model = Model([proposal("alpha"), proposal("beta"), proposal("join", ("alpha", "beta"))])
    events = [
        i
        async for i in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Root secret not needed by workers"}],
            registry=registry(read),
            context=ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"),
            react_enabled=True,
            planning_enabled=True,
            parallel_enabled=True,
            parallel_max_workers=2,
            limits=AgentLimits(max_total_tokens=80_000, max_steps=10),
        )
    ]
    snapshots = [
        e.planning_snapshot for e in events if isinstance(e, AgentLoopEvent) and e.planning_sync
    ]
    last = snapshots[-1]
    assert last["statuses"] == {"alpha": "succeeded", "beta": "succeeded", "join": "succeeded"}
    assert last["worker_batches"][0]["overlapped"]
    for messages, tools in model.requests:
        brief = next(
            (m["content"] for m in messages if "HOST_TASK:" in (m.get("content") or "")), None
        )
        if not brief:
            continue
        data = json.loads(brief.split("\n", 1)[1])
        key = data["task"]["task_id"]
        assert "Root secret" not in json.dumps(messages)
        assert {t["function"]["name"] for t in tools} == {"read_value", "finish_task"}
        if key in {"alpha", "beta"}:
            other = "beta" if key == "alpha" else "alpha"
            assert "Secret observation " + other not in json.dumps(messages)
            assert not data["dependency_results"]
        else:
            assert set(data["dependency_results"]) == {"alpha", "beta"}
    started = [
        e.step_number for e in events if isinstance(e, AgentLoopEvent) and e.kind == "model_started"
    ]
    assert len(started) == len(set(started)) == 8
    calls = [
        e.tool_call.id for e in events if isinstance(e, AgentLoopEvent) and e.kind == "tool_started"
    ]
    assert len(calls) == len(set(calls))
    usage = [
        e.usage.total_tokens
        for e in events
        if isinstance(e, AgentLoopEvent) and e.kind == "model_completed"
    ]
    final = [i for i in events if isinstance(i, LLMChunk) and i.usage][-1]
    assert final.usage.total_tokens == sum(usage) == 200
    assert "".join(i.content for i in events if isinstance(i, LLMChunk)) == "Workers complete"


async def test_shared_budget_blocks_overlapping_and_unknown_reservations():
    budget = SharedWorkerBudget(100)
    async with budget.reserve(70, unreserved_usage) as receipt:
        with pytest.raises(Exception, match="ngân sách chung"):
            async with budget.reserve(40, unreserved_usage):
                pytest.fail("Must not admit")
        receipt.actual = 20
    assert (budget.spent, budget.inflight) == (20, 0)
    async with budget.reserve(50, unreserved_usage):
        pass
    assert budget.held == 50 and budget.stopped
    with pytest.raises(Exception, match="ngân sách chung"):
        async with budget.reserve(1, unreserved_usage):
            pytest.fail("Unknown billing must stop admissions")


async def test_quota_receipts_are_context_local(monkeypatch):
    settled = []

    async def consume(*args, **kwargs):
        pass

    async def settle(*args, reserved, actual, **kwargs):
        settled.append((reserved, actual))

    monkeypatch.setattr(usage_limits, "consume_usage_limits", consume)
    monkeypatch.setattr(usage_limits, "settle_reserved_usage", settle)
    guard = AgentQuotaGuard(user_id=uuid4(), token_limit=1000, tool_call_limit=20)
    barrier = asyncio.Event()
    receipts = []

    async def worker(amount):
        async with guard.reserve_tokens(amount) as receipt:
            receipts.append(receipt)
            if len(receipts) == 2:
                barrier.set()
            await barrier.wait()
            await guard.record_event(
                AgentLoopEvent(
                    kind="model_completed",
                    step_number=amount,
                    usage=TokenUsage(total_tokens=amount // 2),
                )
            )
            assert receipt is guard.active_reservation

    await asyncio.gather(worker(100), worker(200))
    assert guard.active_reservation is None and sorted(settled) == [(100, 50), (200, 100)]


def test_ready_waves_and_terminal_reducer_are_bounded():
    async def read(context, args):
        return ToolResult.success("Value")

    tools = registry(read)
    plan = dag.create_plan(
        {"tasks": [proposal("alpha"), proposal("beta"), proposal("join", ("alpha",))]},
        goal="Test",
        registry=tools,
        role="user",
        remaining_tokens=50_000,
        remaining_steps=10,
        remaining_seconds=100,
    )
    assert [t.task_id for t in ready_wave(plan, tools, 1)] == ["alpha"]
    assert [t.task_id for t in ready_wave(plan, tools, 3)] == ["alpha", "beta"]
    result = {
        "plan_version": 1,
        "task_id": "alpha",
        "attempt_id": "attempt1",
        "status": "succeeded",
        "summary": "OK",
    }
    merged = merge_result(plan, result, {"first_step": 2, "last_step": 3})
    assert merge_result(merged, {**result, "summary": "Late replacement"}, {}) == merged
    assert merge_result(merged, {**result, "plan_version": 2}, {}) == merged
    assert [t.task_id for t in ready_wave(merged, tools, 3)] == ["beta", "join"]


async def test_worker_failure_blocks_dependents_but_keeps_independent_result():
    async def read(context, args):
        if args["key"] == "alpha":
            return ToolResult.failure("No data", error_code="fixture_failure")
        return ToolResult.success("Value")

    model = Model([proposal("alpha"), proposal("beta"), proposal("join", ("alpha",))])
    events = [
        i
        async for i in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Build a plan"}],
            registry=registry(read),
            context=ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"),
            react_enabled=True,
            planning_enabled=True,
            parallel_enabled=True,
            limits=AgentLimits(max_total_tokens=80_000, max_steps=10),
        )
    ]
    snapshots = [
        e.planning_snapshot for e in events if isinstance(e, AgentLoopEvent) and e.planning_sync
    ]
    assert snapshots[-1]["statuses"] == {"alpha": "failed", "beta": "succeeded", "join": "blocked"}


async def test_capacity_cancellation_releases_user_and_global_slots():
    capacity = WorkerCapacity(1, 1)
    entered = asyncio.Event()

    async def holder():
        async with capacity.slot("user"):
            entered.set()
            await asyncio.Event().wait()

    job = asyncio.create_task(holder())
    await entered.wait()
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    async with asyncio.timeout(1), capacity.slot("user"):
        assert "user" in capacity.users
    assert not capacity.users
