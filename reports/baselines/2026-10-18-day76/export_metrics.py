"""Export only aggregate/synthetic eval metadata, without user conversations or secrets."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402

from backend.core.config import get_settings  # noqa: E402
from backend.database import SessionFactory, engine  # noqa: E402
from backend.models import AgentRun, EvalCaseResult  # noqa: E402
from backend.services.admin_metrics import get_admin_overview  # noqa: E402
from backend.services.evaluation_metrics import load_eval_run_metrics  # noqa: E402


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 2)


async def main() -> None:
    run_id = UUID(sys.argv[1])
    try:
        async with SessionFactory() as session:
            report = await load_eval_run_metrics(session, run_id)
            if report is None:
                raise SystemExit("Requested eval run not found")
            rows = list(
                await session.scalars(
                    select(EvalCaseResult)
                    .where(EvalCaseResult.eval_run_id == run_id)
                    .order_by(EvalCaseResult.case_id)
                )
            )
            overview = await get_admin_overview(session, window_days=7)
            stale = list(
                await session.scalars(
                    select(AgentRun).where(
                        AgentRun.status.in_(("running", "waiting_approval")),
                        AgentRun.created_at
                        < datetime.now(UTC)
                        - timedelta(minutes=get_settings().admin_alert_stuck_run_minutes),
                    )
                )
            )
            postgres_version = await session.scalar(text("SHOW server_version"))
            extensions = [
                dict(r)
                for r in (
                    await session.execute(
                        text("SELECT extname, extversion FROM pg_extension ORDER BY extname")
                    )
                ).mappings()
            ]
        latencies = [row.latency_ms for row in rows]
        fields = [
            "case_id",
            "category",
            "status",
            "score",
            "critical_passed",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "judge_tokens",
        ]
        payload = {
            "evaluation": report.to_payload(),
            "cases": [{field: getattr(row, field) for field in fields} for row in rows],
            "agent_latency_ms": {
                "samples": len(latencies),
                "mean": round(sum(latencies) / len(latencies), 2),
                "p50": percentile(latencies, 0.5),
                "p95": percentile(latencies, 0.95),
                "min": min(latencies),
                "max": max(latencies),
                "method": "linear interpolation, equivalent to percentile_cont",
                "scope": "agent execution only; excludes judge and CLI startup",
            },
            "tokens": {
                "agent_prompt": sum(r.prompt_tokens for r in rows),
                "agent_completion": sum(r.completion_tokens for r in rows),
                "agent_total": sum(r.total_tokens for r in rows),
                "judge_total": sum(r.judge_tokens for r in rows),
                "agent_plus_judge_total": sum(r.total_tokens + r.judge_tokens for r in rows),
                "scope": "two eval smoke cases; HTTP E2E and other checks excluded",
            },
            "admin_7_day_snapshot": {
                "from_time": overview.from_time.isoformat(),
                "to_time": overview.to_time.isoformat(),
                "activity": overview.activity.model_dump(),
                "latency": overview.latency.model_dump(),
                "alerts": [a.model_dump() for a in overview.alerts],
                "stale_active_runs": [
                    {
                        "run_id": str(r.id),
                        "status": r.status,
                        "created_at": r.created_at.isoformat(),
                        "current_step": r.current_step,
                    }
                    for r in stale
                ],
                "scope": "existing application data, not the isolated eval smoke sample",
            },
            "database": {"server_version": postgres_version, "extensions": extensions},
        }
        Path(__file__).with_name("live-metrics.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "run_id": str(run_id),
                    "pass_rate": report.metrics.pass_rate,
                    "latency_ms": payload["agent_latency_ms"],
                    "tokens": payload["tokens"],
                    "database": payload["database"],
                    "admin_alerts": payload["admin_7_day_snapshot"]["alerts"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if report.status != "completed" or report.metrics.pass_rate != 1:
            raise SystemExit("Eval quality gate did not pass")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
