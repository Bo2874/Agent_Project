import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from agent_core.graph import ToolCallRequest
from agent_core.tools import ToolContext
from backend.core.config import get_settings


class ToolApprovalNotFoundError(LookupError):
    pass


class ToolApprovalForbiddenError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class PendingToolApproval:
    approval_id: UUID
    run_id: UUID
    conversation_id: UUID
    user_id: UUID
    tool_call: ToolCallRequest
    decision: asyncio.Future[bool]


class ToolApprovalManager:
    """Coordinate one-time approval decisions inside the current backend process."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[UUID, PendingToolApproval] = {}

    async def wait_for_decision(
        self,
        context: ToolContext,
        approval_id: str,
        tool_call: ToolCallRequest,
    ) -> bool:
        if context.run_id is None:
            return False
        from backend.services.run_events import current_job

        if current_job.get() is not None:
            return await self.wait_durable(context, approval_id, tool_call)

        parsed_id = UUID(approval_id)
        loop = asyncio.get_running_loop()
        pending = PendingToolApproval(
            approval_id=parsed_id,
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            user_id=context.user_id,
            tool_call=tool_call,
            decision=loop.create_future(),
        )
        self._pending[parsed_id] = pending
        try:
            return await asyncio.wait_for(
                asyncio.shield(pending.decision),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return False
        finally:
            self._pending.pop(parsed_id, None)

    def resolve(
        self,
        *,
        approval_id: UUID,
        run_id: UUID,
        conversation_id: UUID,
        user_id: UUID,
        approved: bool,
    ) -> None:
        pending = self._pending.get(approval_id)
        if pending is None or pending.run_id != run_id:
            raise ToolApprovalNotFoundError("Yêu cầu phê duyệt không còn hiệu lực")
        if pending.conversation_id != conversation_id or pending.user_id != user_id:
            raise ToolApprovalForbiddenError("Không có quyền phê duyệt tool call này")
        if pending.decision.done():
            raise ToolApprovalNotFoundError("Yêu cầu phê duyệt đã được xử lý")
        pending.decision.set_result(approved)

    async def wait_durable(self, context, approval_id, tool_call):
        from sqlalchemy import select

        from backend.models import AgentApproval
        from backend.services.run_events import fingerprint, runtime_session

        digest = fingerprint(
            {
                "tool": tool_call.name,
                "arguments": dict(tool_call.arguments),
                "task": context.task_id,
            }
        )
        async with runtime_session() as session:
            row = await session.get(AgentApproval, UUID(approval_id))
            if row is None:
                session.add(
                    AgentApproval(
                        id=UUID(approval_id),
                        agent_run_id=context.run_id,
                        fingerprint=digest,
                        expires_at=datetime.now(UTC) + timedelta(seconds=self.timeout_seconds),
                    )
                )
                await session.commit()
        while True:
            async with runtime_session() as session:
                row = await session.scalar(
                    select(AgentApproval).where(
                        AgentApproval.id == UUID(approval_id),
                        AgentApproval.agent_run_id == context.run_id,
                    )
                )
                if row is None or row.fingerprint != digest or row.expires_at <= datetime.now(UTC):
                    return False
                if row.decision:
                    return row.decision == "approved"
            await asyncio.sleep(0.5)

    async def resolve_durable(self, *, approval_id, run_id, conversation_id, user_id, approved):
        from sqlalchemy import select

        from backend.database import SessionFactory
        from backend.models import AgentApproval, AgentRun, Conversation

        async with SessionFactory() as session:
            row = await session.scalar(
                select(AgentApproval)
                .join(AgentRun)
                .join(Conversation)
                .where(
                    AgentApproval.id == approval_id,
                    AgentRun.id == run_id,
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                    AgentRun.status.in_(["running", "waiting_approval"]),
                )
                .with_for_update(of=AgentApproval)
            )
            if row is None:
                return False
            if row.decision is not None or row.expires_at <= datetime.now(UTC):
                raise ToolApprovalNotFoundError("Yêu cầu phê duyệt đã hết hạn hoặc đã xử lý")
            row.decision = "approved" if approved else "denied"
            await session.commit()
            return True


tool_approval_manager = ToolApprovalManager(
    timeout_seconds=get_settings().tool_approval_timeout_seconds
)
