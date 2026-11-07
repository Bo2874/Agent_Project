"""Transactional result inbox; the database is authoritative after reconnect."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.models.automation import Automation, AutomationRun
from backend.models.notification import UserNotification

LABELS = {
    "succeeded": "Hoàn tất",
    "failed": "Chưa hoàn tất",
    "cancelled": "Đã dừng",
    "waiting_approval": "Cần phê duyệt",
    "needs_reconciliation": "Cần kiểm tra kết quả",
    "missed": "Đã lỡ lịch",
    "skipped_overlap": "Bỏ qua vì lượt trước còn hoạt động",
}


async def queue_result_notification(session, run_id):
    row = await session.get(AutomationRun, run_id)
    if row is None or row.status not in LABELS:
        return
    auto = await session.get(Automation, row.automation_id)
    event_key = f"automation:{row.id}:{row.status}"
    if row.status == "waiting_approval" and row.agent_run_id:
        from backend.models import RunStep, ToolCall

        approval_id = await session.scalar(
            select(ToolCall.approval_id)
            .join(RunStep, RunStep.id == ToolCall.run_step_id)
            .where(
                RunStep.agent_run_id == row.agent_run_id,
                ToolCall.status == "waiting_approval",
                ToolCall.approval_decision.is_(None),
                ToolCall.approval_id.is_not(None),
            )
            .order_by(ToolCall.created_at.desc(), ToolCall.id.desc())
            .limit(1)
        )
        if approval_id:
            event_key += f":{approval_id}"
    await session.execute(
        insert(UserNotification)
        .values(
            owner_id=row.owner_id,
            event_key=event_key,
            title=f"{auto.name} · {LABELS[row.status]}",
            result={
                "automation_run_id": str(row.id),
                "automation_id": str(row.automation_id),
                "status": row.status,
            },
        )
        .on_conflict_do_nothing(constraint="uq_notification_event")
    )


async def notify_automation_result(session, run_id):
    await queue_result_notification(session, run_id)
    await session.commit()
