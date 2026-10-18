import asyncio
from dataclasses import dataclass
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


tool_approval_manager = ToolApprovalManager(
    timeout_seconds=get_settings().tool_approval_timeout_seconds
)
