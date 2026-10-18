from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from backend.dependencies import CurrentAdmin, DbSession
from backend.schemas.admin import (
    AdminAgentRunDetail,
    AdminAgentRunsResponse,
    AdminAuditLogsResponse,
    AdminEvalComparisonResponse,
    AdminEvalRunsResponse,
    AdminOverviewResponse,
)
from backend.services.admin_agent_runs import (
    get_admin_agent_run_detail,
    list_admin_agent_runs,
)
from backend.services.admin_evaluations import (
    compare_admin_eval_runs,
    list_admin_eval_runs,
)
from backend.services.admin_metrics import get_admin_overview
from backend.services.audit_logs import list_admin_audit_logs, record_audit_event

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(
    session: DbSession,
    _: CurrentAdmin,
    days: int = Query(default=7, ge=1, le=90),
) -> AdminOverviewResponse:
    return await get_admin_overview(session, window_days=days)


@router.get("/agent-runs", response_model=AdminAgentRunsResponse)
async def admin_agent_runs(
    session: DbSession,
    _: CurrentAdmin,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
) -> AdminAgentRunsResponse:
    return await list_admin_agent_runs(session, window_days=days, limit=limit)


@router.get("/agent-runs/{run_id}", response_model=AdminAgentRunDetail)
async def admin_agent_run_detail(
    run_id: UUID,
    session: DbSession,
    admin: CurrentAdmin,
) -> AdminAgentRunDetail:
    detail = await get_admin_agent_run_detail(session, run_id=run_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy agent run",
        )
    await record_audit_event(
        session,
        actor_user_id=admin.id,
        action="admin.agent_run.read",
        target_type="agent_run",
        target_id=str(run_id),
        metadata={"step_count": len(detail.steps), "status": detail.status},
    )
    return detail


@router.get("/audit-logs", response_model=AdminAuditLogsResponse)
async def admin_audit_logs(
    session: DbSession,
    _: CurrentAdmin,
    limit: int = Query(default=20, ge=1, le=100),
) -> AdminAuditLogsResponse:
    return await list_admin_audit_logs(session, limit=limit)


@router.get("/evals", response_model=AdminEvalRunsResponse)
async def admin_eval_runs(
    session: DbSession,
    _: CurrentAdmin,
    limit: int = Query(default=20, ge=1, le=100),
) -> AdminEvalRunsResponse:
    return await list_admin_eval_runs(session, limit=limit)


@router.get("/evals/compare", response_model=AdminEvalComparisonResponse)
async def admin_eval_comparison(
    session: DbSession,
    admin: CurrentAdmin,
    baseline_id: UUID,
    candidate_id: UUID,
) -> AdminEvalComparisonResponse:
    if baseline_id == candidate_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Baseline và candidate phải khác nhau",
        )
    comparison = await compare_admin_eval_runs(
        session,
        baseline_id=baseline_id,
        candidate_id=candidate_id,
    )
    if comparison is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy đủ hai eval run để so sánh",
        )
    await record_audit_event(
        session,
        actor_user_id=admin.id,
        action="admin.eval.compare",
        target_type="eval_run",
        target_id=str(candidate_id),
        metadata={"baseline_id": str(baseline_id), "comparable": comparison.comparable},
    )
    return comparison
