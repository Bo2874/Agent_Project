"""Focused durable graph tests without paid APIs."""

from copy import deepcopy
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_task_workers import Model, proposal, registry

from agent_core.graph import AgentLimits, AgentLoopEvent, stream_agent_graph
from agent_core.graph.runtime import AgentNodeExecutionError
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


async def test_completed_workers_recover_without_repeating_calls_after_root_fanin_failure():
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
        limits=AgentLimits(max_total_tokens=80_000, max_steps=8),
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


def test_replay_cursor_is_bound_to_run():
    run = uuid4()
    assert event_cursor(run, None) == 0
    assert event_cursor(run, f"{run}:123") == 123
    for value in (f"{uuid4()}:2", "-1", f"{run}:not-int"):
        with pytest.raises(ValueError):
            event_cursor(run, value)
