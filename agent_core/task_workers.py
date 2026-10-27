"""Host-owned fan-out admission and deterministic task result reduction.

Workers use the existing ReAct graph with private messages/checkpoints. The parent
keeps authority over scheduling, final synthesis and the shared token ledger.
"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from weakref import WeakKeyDictionary

from agent_core.contracts import PlanSpec, TaskResult
from agent_core.graph.budgeting import UsageReservation
from agent_core.graph.runtime import AgentQuotaLimitError

_capacities = WeakKeyDictionary()


class WorkerCapacity:
    """Process-local user/global slots; durable distributed leases are stage 6."""

    def __init__(self, global_maximum, user_maximum):
        self.global_slots = asyncio.Semaphore(global_maximum)
        self.user_maximum = user_maximum
        self.users = {}

    @asynccontextmanager
    async def slot(self, user):
        semaphore, count = self.users.get(user, (asyncio.Semaphore(self.user_maximum), 0))
        self.users[user] = semaphore, count + 1
        try:
            async with semaphore, self.global_slots:
                yield
        finally:
            semaphore, count = self.users[user]
            if count == 1:
                del self.users[user]
            else:
                self.users[user] = semaphore, count - 1


def process_capacity(global_maximum, user_maximum):
    loop = asyncio.get_running_loop()
    capacities = _capacities.setdefault(loop, {})
    return capacities.setdefault(
        (global_maximum, user_maximum), WorkerCapacity(global_maximum, user_maximum)
    )


class SharedWorkerBudget:
    """Atomic admission across siblings; unknown spend keeps its reservation.

    Provider actual usage can exceed estimates. Count the full overrun and block
    subsequent admissions; already in-flight provider calls cannot be undone.
    """

    def __init__(self, available):
        self.available = max(0, available)
        self.spent = 0
        self.held = 0
        self.inflight = 0
        self.stopped = False
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def reserve(self, amount, upstream):
        async with self.lock:
            if self.stopped or amount > self.available - self.spent - self.held - self.inflight:
                raise AgentQuotaLimitError("Không đủ ngân sách chung cho worker.")
            self.inflight += amount
        receipt = UsageReservation()
        admitted = False
        try:
            async with upstream(amount) as receipt:
                admitted = True
                yield receipt
        finally:
            async with self.lock:
                self.inflight -= amount
                if admitted:
                    if receipt.actual is None:
                        self.held += max(amount, receipt.known_tokens)
                        self.stopped = True
                    else:
                        self.spent += receipt.actual
                        if self.spent + self.held > self.available:
                            self.stopped = True


def names_for_task(task, registry):
    return frozenset(
        tool.name
        for tool in registry.list_tools()
        if (
            registry.get(tool.name).capability.capability_id
            if registry.get(tool.name).capability
            else tool.name
        )
        in task.allowed_capability_ids
    )


def ready_wave(planning, registry, maximum):
    """Read-only tasks may fan out; approval/write/unknown tasks are barriers.

    Declaration order resolves ties. A ready write is never co-scheduled with
    reads; the existing root approval/checkpoint path handles that task alone.
    """
    ready = [
        t
        for t in PlanSpec.model_validate(planning["spec"]).tasks
        if planning["statuses"][t.task_id] == "pending"
        and all(planning["statuses"][d] == "succeeded" for d in t.depends_on)
    ]
    selected = []
    for task in ready:
        names = names_for_task(task, registry)
        if task.side_effect != "read_only" or any(
            registry.get(n).policy.requires_approval for n in names
        ):
            break
        selected.append(task)
        if len(selected) == maximum:
            break
    return selected


def worker_seed(planning, task, elapsed):
    value = deepcopy(planning)
    # Only dependency results cross the worker boundary. No sibling transcripts.
    value["results"] = {d: value["results"][d] for d in task.depends_on}
    value["active_task"] = task.task_id
    value["statuses"][task.task_id] = "running"
    value["attempt"] = {
        "first_step": 1,
        "tokens_start": 0,
        "observation_offset": 0,
        "start_elapsed": elapsed,
    }
    value["attempts"] = {}
    return value


def merge_result(planning, raw, attempt):
    """Idempotent terminal reducer; stale versions and late duplicates cannot win."""
    result = TaskResult.model_validate(raw)
    if (
        result.plan_version != planning["spec"]["plan_version"]
        or result.task_id not in planning["statuses"]
        or result.attempt_id != "attempt1"
        or result.task_id in planning["results"]
    ):
        return planning
    value = deepcopy(planning)
    value["results"][result.task_id] = result.model_dump(mode="json")
    value["statuses"][result.task_id] = result.status
    value.setdefault("attempts", {})[result.task_id] = {**attempt, "result": raw}
    # Keep snapshot ordering independent of provider completion order.
    value["results"] = {
        t["task_id"]: value["results"][t["task_id"]]
        for t in value["spec"]["tasks"]
        if t["task_id"] in value["results"]
    }
    return value
