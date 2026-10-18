from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.redaction import redact_data
from backend.models import AuditLog
from backend.schemas.admin import AdminAuditLogItem, AdminAuditLogsResponse


async def record_audit_event(
    session: AsyncSession,
    *,
    actor_user_id: UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    metadata: Mapping[str, Any] | None = None,
) -> AuditLog:
    if outcome not in {"success", "denied", "failure"}:
        raise ValueError("Audit outcome không hợp lệ")
    if not action.strip() or len(action) > 100:
        raise ValueError("Audit action không hợp lệ")
    safe_metadata = redact_data(dict(metadata or {}))
    audit_log = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        metadata_json=safe_metadata,
    )
    session.add(audit_log)
    await session.commit()
    await session.refresh(audit_log)
    return audit_log


async def list_admin_audit_logs(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> AdminAuditLogsResponse:
    if not 1 <= limit <= 100:
        raise ValueError("limit phải từ 1 đến 100")
    rows = list(
        (
            await session.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
            )
        ).all()
    )
    return AdminAuditLogsResponse(
        logs=[
            AdminAuditLogItem(
                id=item.id,
                actor_user_id=item.actor_user_id,
                action=item.action,
                target_type=item.target_type,
                target_id=item.target_id,
                outcome=item.outcome,
                metadata=redact_data(item.metadata_json),
                created_at=item.created_at,
            )
            for item in rows
        ]
    )
