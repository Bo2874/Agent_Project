"""Focused durable graph tests without paid APIs."""

from copy import deepcopy
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_task_workers import Model, proposal, registry

from agent_core.graph import AgentLimits, AgentLoopEvent, stream_agent_graph
from agent_core.graph.runtime import AgentNodeExecutionError
from agent_core.llm.types import TokenUsage
from agent_core.tools import ToolContext, ToolResult
from backend.services.run_events import event_cursor


class Journal:
    def __init__(self):
        self.rows = {}

    async def load(self, key, digest):
        row = self.rows.get(key)
        if row and (row[0] != digest or row[1] is None):
            raise AgentNodeExecutionError("Unknown outcome", code="agent_operation_unknown")
        return deepcopy(row[1]) if row else None

    async def begin(self, key, digest):
        assert key not in self.rows
        self.rows[key] = digest, None, 0

    async def finish(self, key, result, tokens):
        self.rows[key] = self.rows[key][0], deepcopy(result), tokens

    async def total_spent(self):
        return sum(row[2] for row in self.rows.values())

    async def allocate_step(self, key, minimum):
        cache_key = "step." + key
        if cache_key not in self.rows:
            previous = [r[1]["step"] for k, r in self.rows.items() if k.startswith("step.")]
            step = max([minimum - 1, *previous]) + 1
            self.rows[cache_key] = key, {"step": step}, 0
        return self.rows[cache_key][1]["step"]


@pytest.mark.parametrize("unbounded", [False, True])
async def test_completed_workers_recover_without_repeating_calls_after_root_fanin_failure(
    unbounded,
):
    calls = []

    async def read(context, args):
        calls.append(args["key"])
        return ToolResult.success("Real fixture observation")

    model = Model([proposal("alpha"), proposal("beta")])
    saver, journal = InMemorySaver(), Journal()
    context = ToolContext(
        user_id=uuid4(), conversation_id=uuid4(), run_id=uuid4(), user_role="user"
    )
    fail = True

    async def interrupt_fanin(event):
        nonlocal fail
        if (
            fail
            and event.planning_sync
            and event.planning_snapshot
            and set(event.planning_snapshot["statuses"].values()) == {"succeeded"}
        ):
            fail = False
            raise RuntimeError("Simulate interrupted root fan-in persistence")

    arguments = dict(
        model=model,
        messages=[{"role": "user", "content": "Plan two independent reads"}],
        registry=registry(read),
        context=context,
        event_handler=interrupt_fanin,
        react_enabled=True,
        planning_enabled=True,
        parallel_enabled=True,
        graph_checkpointer=saver,
        operation_journal=journal,
        limits=AgentLimits(
            max_total_tokens=80_000,
            max_steps=None if unbounded else 8,
            max_duration_seconds=None if unbounded else 120,
        ),
    )
    with pytest.raises(RuntimeError, match="interrupted root"):
        _ = [e async for e in stream_agent_graph(**arguments)]
    before = len(model.requests)
    events = [e async for e in stream_agent_graph(**arguments, resume=True)]
    assert sorted(calls) == ["alpha", "beta"]
    assert len(model.requests) == before + 1  # Only root final synthesis is new.
    snapshots = [
        e.planning_snapshot for e in events if isinstance(e, AgentLoopEvent) and e.planning_sync
    ]
    assert set(snapshots[-1]["statuses"].values()) == {"succeeded"}


async def test_unknown_operation_blocks_repeat_and_changed_arguments():
    journal = Journal()
    await journal.begin("root.tool.1.send", "original")
    with pytest.raises(AgentNodeExecutionError):
        await journal.load("root.tool.1.send", "original")
    await journal.finish("root.tool.1.send", {"content": "sent"}, 10)
    assert await journal.load("root.tool.1.send", "original") == {"content": "sent"}
    with pytest.raises(AgentNodeExecutionError):
        await journal.load("root.tool.1.send", "changed")


async def test_terminal_worker_resume_keeps_root_above_progress_only_step():
    calls = []

    async def read(context, args):
        calls.append(args["key"])
        return ToolResult.success("Observed", usage=TokenUsage(total_tokens=12_000))

    task = {**proposal("alpha"), "max_tokens": 10_000}
    model, saver, journal = Model([task]), InMemorySaver(), Journal()
    fail = True

    async def interrupt_fanin(event):
        nonlocal fail
        if (
            fail
            and event.planning_sync
            and event.planning_snapshot
            and set(event.planning_snapshot["statuses"].values()) == {"failed"}
        ):
            fail = False
            raise RuntimeError("Interrupted failed fan-in")

    arguments = dict(
        model=model,
        messages=[{"role": "user", "content": "Plan one read"}],
        registry=registry(read),
        context=ToolContext(
            user_id=uuid4(), conversation_id=uuid4(), run_id=uuid4(), user_role="user"
        ),
        event_handler=interrupt_fanin,
        react_enabled=True,
        planning_enabled=True,
        parallel_enabled=True,
        graph_checkpointer=saver,
        operation_journal=journal,
        limits=AgentLimits(max_total_tokens=80_000),
    )
    with pytest.raises(RuntimeError, match="Interrupted failed fan-in"):
        _ = [e async for e in stream_agent_graph(**arguments)]
    mapped_high_water = max(r[1]["step"] for k, r in journal.rows.items() if k.startswith("step."))
    events = [e async for e in stream_agent_graph(**arguments, resume=True)]
    assert calls == ["alpha"]
    root_steps = [
        e.step_number for e in events if isinstance(e, AgentLoopEvent) and e.kind == "model_started"
    ]
    assert root_steps and min(root_steps) > mapped_high_water


def test_replay_cursor_is_bound_to_run():
    run = uuid4()
    assert event_cursor(run, None) == 0
    assert event_cursor(run, f"{run}:123") == 123
    for value in (f"{uuid4()}:2", "-1", f"{run}:not-int"):
        with pytest.raises(ValueError):
            event_cursor(run, value)
