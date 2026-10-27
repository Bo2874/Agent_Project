"""Durable root jobs. PostgreSQL queue owns leases, LangGraph owns execution state."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from agent_core.context_budget import ContextBlock, ContextTokenBudget
from agent_core.graph import AgentLimits
from agent_core.graph.checkpointing import agent_checkpoint_config
from agent_core.skills import SkillSelectionConfig
from agent_core.tools import ToolContext, ToolSelectionConfig
from backend.background.queue import BackgroundJobOwnershipError, PostgresJobQueue
from backend.core.config import get_settings
from backend.database import SessionFactory
from backend.models import AgentOperation, AgentRun, BackgroundJob, Conversation, RunEvent, User
from backend.services.run_events import (
    OperationJournal,
    append_event,
    append_packet,
    current_job,
    fingerprint,
    runtime_session,
)


async def enqueue_run(arguments):
    run_id = arguments["agent_run_id"]
    payload = {k: str(arguments[k]) for k in ("conversation_id", "user_message_id", "user_id")}
    payload.update(
        model_messages=arguments["model_messages"],
        model=arguments["model"].model,
        user_message_content=arguments["user_message_content"],
        user_message_observed_at=arguments["user_message_observed_at"].isoformat(),
        limits=asdict(arguments["limits"]),
        context_budget=asdict(arguments["context_budget"]),
        tool_selection_config=asdict(arguments["tool_selection_config"]),
        skill_selection_config=asdict(arguments["skill_selection_config"]),
        history_summary_context=[asdict(b) for b in arguments["history_summary_context"]],
        query_messages=arguments.get("query_messages"),
        rag_search_mode=arguments["rag_search_mode"],
    )
    async with SessionFactory() as session:
        conversation = await session.scalar(
            select(Conversation)
            .where(Conversation.id == arguments["conversation_id"])
            .with_for_update()
        )
        active = await session.scalar(
            select(BackgroundJob.id)
            .join(AgentRun, BackgroundJob.agent_run_id == AgentRun.id)
            .where(
                AgentRun.conversation_id == conversation.id,
                BackgroundJob.status.in_(["queued", "running"]),
                AgentRun.id != run_id,
            )
            .limit(1)
        )
        if active:
            raise ValueError("Hội thoại đang có một yêu cầu chạy nền.")
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        saved = await session.scalar(
            select(AgentOperation).where(
                AgentOperation.agent_run_id == run_id, AgentOperation.key == "input"
            )
        )
        if saved is None:
            session.add(
                AgentOperation(
                    agent_run_id=run_id,
                    key="input",
                    status="completed",
                    fingerprint=fingerprint(payload),
                    result=payload,
                )
            )
        run.status = "queued"
        after = run.event_sequence
        run.event_sequence += 1
        session.add(
            RunEvent(
                agent_run_id=run_id,
                sequence=run.event_sequence,
                kind="queued",
                data={"run_id": str(run_id), "message": "Đã xếp hàng xử lý nền."},
            )
        )
        run.rag_trace = {**(run.rag_trace or {}), "durable": True}
        settings = get_settings()
        await PostgresJobQueue().enqueue(
            job_type="agent.run",
            payload={"run_id": str(run_id)},
            queue_name=settings.background_queue_name,
            user_id=arguments["user_id"],
            agent_run_id=run_id,
            max_attempts=3,
            idempotency_key=f"agent-{run_id}-resume{run.resume_count}",
            session=session,
        )
        await session.commit()
    await arguments["model"].aclose()
    return after


async def agent_run_handler(context, payload):
    from backend.dependencies_llm import create_chat_model
    from backend.dependencies_skills import get_skill_registry
    from backend.dependencies_tools import get_tool_registry
    from backend.routes.chat import stream_assistant_response
    from backend.services.agent_runs import fail_agent_run
    from backend.services.langgraph_checkpoints import get_langgraph_checkpointer

    if set(payload) != {"run_id"}:
        raise ValueError("Invalid agent job payload")
    run_id = UUID(payload["run_id"])
    token = current_job.set(context.job)
    try:
        async with runtime_session() as session:
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update()
            )
            owner = await session.scalar(
                select(User)
                .join(Conversation, Conversation.user_id == User.id)
                .where(Conversation.id == run.conversation_id, User.is_active)
            )
            if owner is None:
                raise ValueError("Run owner unavailable")
            row = await session.scalar(
                select(AgentOperation).where(
                    AgentOperation.agent_run_id == run_id, AgentOperation.key == "input"
                )
            )
            data = dict(row.result)
            if str(owner.id) != data["user_id"] or context.job.id != (
                await session.scalar(
                    select(BackgroundJob.id).where(
                        BackgroundJob.id == context.job.id, BackgroundJob.agent_run_id == run_id
                    )
                )
            ):
                raise ValueError("Run job scope changed")
            if run.status == "completed":
                return {"run_id": str(run_id), "status": "completed"}
            unknown = await session.scalar(
                select(AgentOperation.id)
                .where(AgentOperation.agent_run_id == run_id, AgentOperation.status == "started")
                .limit(1)
            )
            if unknown:
                run.status, run.stop_reason, run.error_message = (
                    "failed",
                    "agent_operation_unknown",
                    "Lời gọi trước chưa rõ kết quả; không tự gọi lại.",
                )
                await session.commit()
                await fail_agent_run(
                    run_id,
                    "failed",
                    "Lời gọi trước chưa rõ kết quả.",
                    stop_reason="agent_operation_unknown",
                )
                await append_event(
                    run_id,
                    "error",
                    {
                        "message": "Lời gọi trước chưa rõ kết quả; "
                        "cần kiểm tra trước khi gửi yêu cầu mới.",
                        "code": "agent_operation_unknown",
                        "resumable": False,
                    },
                )
                return {"run_id": str(run_id), "status": "failed"}
            trace = dict(run.rag_trace or {})
            trace.setdefault("runtime_started_at", datetime.now(UTC).isoformat())
            run.rag_trace, run.status = trace, "running"
            await session.commit()
            role, started = owner.role, datetime.fromisoformat(trace["runtime_started_at"])
        saver = get_langgraph_checkpointer()
        resume = await saver.aget_tuple(agent_checkpoint_config(run_id)) is not None
        if resume:
            await append_event(run_id, "reset", {"message": "Đang khôi phục từ checkpoint."})
        model = create_chat_model()
        if model.model != data["model"]:
            await model.aclose()
            raise ValueError("Model configuration changed")
        buffer = []

        async def flush():
            if buffer:
                await append_event(run_id, "delta", {"content": "".join(buffer)})
                buffer.clear()

        async for packet in stream_assistant_response(
            agent_run_id=run_id,
            conversation_id=UUID(data["conversation_id"]),
            user_message_id=UUID(data["user_message_id"]),
            user_id=UUID(data["user_id"]),
            user_message_content=data["user_message_content"],
            user_message_observed_at=datetime.fromisoformat(data["user_message_observed_at"]),
            model_messages=data["model_messages"],
            model=model,
            registry=get_tool_registry(),
            skill_registry=get_skill_registry(),
            tool_context=ToolContext(
                user_id=UUID(data["user_id"]),
                conversation_id=UUID(data["conversation_id"]),
                run_id=run_id,
                user_role=role,
            ),
            limits=AgentLimits(**data["limits"]),
            context_budget=ContextTokenBudget(**data["context_budget"]),
            tool_selection_config=ToolSelectionConfig(**data["tool_selection_config"]),
            skill_selection_config=SkillSelectionConfig(**data["skill_selection_config"]),
            history_summary_context=tuple(
                ContextBlock(**b) for b in data["history_summary_context"]
            ),
            graph_checkpointer=saver,
            resume=resume,
            rag_search_mode=data["rag_search_mode"],
            query_messages=data.get("query_messages"),
            operation_journal=OperationJournal(run_id),
            elapsed_seconds_floor=(datetime.now(UTC) - started).total_seconds() if resume else 0,
        ):
            if packet.startswith("event: delta\n"):
                import json

                buffer.append(json.loads(packet.split("data: ", 1)[1])["content"])
                if sum(map(len, buffer)) >= 1000:
                    await flush()
            else:
                await flush()
                await append_packet(run_id, packet)
        await flush()
        return {"run_id": str(run_id)}
    except asyncio.CancelledError:
        # Lease validation prevents a replaced worker from marking the new run cancelled.
        async with runtime_session(allow_cancel=True) as session:
            job = await session.get(BackgroundJob, context.job.id)
            requested = job.cancel_requested_at is not None
        await fail_agent_run(
            run_id,
            "cancelled" if requested else "failed",
            "Đã dừng thực thi.",
            stop_reason="user_cancelled" if requested else "worker_interrupted",
        )
        await append_event(
            run_id,
            "cancelled" if requested else "error",
            {"message": "Đã dừng thực thi.", "resumable": not requested},
            allow_cancel=True,
        )
        raise
    except BackgroundJobOwnershipError:
        raise
    except Exception:
        await fail_agent_run(
            run_id, "failed", "Không thể tiếp tục job Agent.", stop_reason="agent_job_failed"
        )
        await append_event(
            run_id,
            "error",
            {
                "message": "Không thể tiếp tục job Agent.",
                "code": "agent_job_failed",
                "resumable": False,
            },
        )
        raise
    finally:
        current_job.reset(token)


async def cancel_run(owner, conversation_id, run_id):
    async with SessionFactory() as session:
        run = await session.scalar(
            select(AgentRun)
            .join(Conversation)
            .where(
                AgentRun.id == run_id,
                Conversation.id == conversation_id,
                Conversation.user_id == owner,
            )
        )
        if run is None:
            return False
        if run.status not in {"queued", "running", "waiting_approval"}:
            return True
        jobs = (
            await session.scalars(
                select(BackgroundJob)
                .where(
                    BackgroundJob.agent_run_id == run_id,
                    BackgroundJob.status.in_(["queued", "running"]),
                )
                .with_for_update()
            )
        ).all()
        for job in jobs:
            job.cancel_requested_at = datetime.now(UTC)
            if job.status == "queued":
                job.status = "cancelled"
        await session.commit()
    if not any(job.status == "running" for job in jobs):
        from backend.services.agent_runs import fail_agent_run

        await fail_agent_run(run_id, "cancelled", "Đã hủy yêu cầu.", stop_reason="user_cancelled")
        await append_event(run_id, "cancelled", {"message": "Đã hủy yêu cầu."})
    return True
