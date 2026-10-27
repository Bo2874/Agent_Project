"""Idempotent DB views of a host-generated checkpoint plan snapshot."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from agent_core.contracts import PlanSpec
from backend.models import AgentTask, TaskAttempt


async def project_tasks(session, run_id, snapshot):
    # A resume replays the authoritative checkpoint snapshot before any dispatch;
    # delete rows published by an interrupted, uncommitted transition.
    if snapshot is None:
        await session.execute(delete(AgentTask).where(AgentTask.agent_run_id == run_id))
        return
    spec = PlanSpec.model_validate(snapshot["spec"])
    await session.execute(
        delete(AgentTask).where(
            AgentTask.agent_run_id == run_id,
            AgentTask.task_id.not_in([t.task_id for t in spec.tasks]),
        )
    )
    await session.execute(
        delete(AgentTask).where(
            AgentTask.agent_run_id == run_id, AgentTask.plan_version != spec.plan_version
        )
    )
    for task in spec.tasks:
        status = snapshot["statuses"][task.task_id]
        result = snapshot["results"].get(task.task_id)
        values = {
            "id": uuid4(),
            "agent_run_id": run_id,
            "plan_version": spec.plan_version,
            "task_id": task.task_id,
            "status": status,
            "spec": task.model_dump(mode="json"),
            "result": result,
            "updated_at": datetime.now(UTC),
        }
        statement = (
            insert(AgentTask)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_agent_task_identity",
                set_={
                    k: v
                    for k, v in values.items()
                    if k not in {"id", "agent_run_id", "plan_version", "task_id"}
                },
            )
            .returning(AgentTask.id)
        )
        task_row_id = (await session.execute(statement)).scalar_one()
        attempt = snapshot.get("attempts", {}).get(task.task_id)
        if snapshot.get("active_task") == task.task_id:
            attempt = snapshot["attempt"]
        if attempt is None:
            await session.execute(
                delete(TaskAttempt).where(TaskAttempt.agent_task_id == task_row_id)
            )
            continue
        values = {
            "id": uuid4(),
            "agent_task_id": task_row_id,
            "attempt_id": "attempt1",
            "status": status,
            "first_step": attempt["first_step"],
            "last_step": attempt.get("last_step"),
            "result": result,
            "updated_at": datetime.now(UTC),
        }
        await session.execute(
            insert(TaskAttempt)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_task_attempt_identity",
                set_={
                    k: v
                    for k, v in values.items()
                    if k not in {"id", "agent_task_id", "attempt_id"}
                },
            )
        )
