"""Routing trace persistence, ownership checks, budget resume and admin aggregates."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

from sqlalchemy import func, select

from agent_core.routing.policy import CircuitBreaker, ManagedDecisionProvider
from backend.database import SessionFactory
from backend.models import AgentRun, Conversation, RoutingTrace


@lru_cache(maxsize=16)
def shared_breaker(base_url: str, model: str, threshold: int, cooldown: float):
    return CircuitBreaker(threshold, cooldown)


def token_sum(attempts, field):
    values = [a[field] for a in attempts if type(a.get(field)) is int and a[field] >= 0]
    return sum(values) if values else None


class BackendDecisionProvider(ManagedDecisionProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observer = self.record_trace
        self.run_id = self.user_id = self.token_recorder = None

    async def bind_run(self, *, user_id, run_id, token_recorder=None):
        self.run_id = self.user_id = self.token_recorder = None
        async with SessionFactory() as session:
            owner = await session.scalar(
                select(AgentRun.id)
                .join(Conversation)
                .where(
                    AgentRun.id == run_id,
                    Conversation.user_id == user_id,
                )
            )
            if owner is None:
                raise RuntimeError("Routing run ownership mismatch")
            row = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(RoutingTrace.provider_calls), 0),
                        func.coalesce(func.sum(RoutingTrace.reserved_tokens), 0),
                    ).where(RoutingTrace.agent_run_id == run_id, RoutingTrace.user_id == user_id)
                )
            ).one()
            self.calls, self.reserved_tokens = int(row[0]), int(row[1])
        self.run_id, self.user_id, self.token_recorder = run_id, user_id, token_recorder

    async def record_trace(self, trace):
        if self.run_id is None:
            return
        attempts = trace.get("attempts", [])
        trace["usage_complete"] = all(
            type(a.get("input_tokens")) is int and type(a.get("output_tokens")) is int
            for a in attempts
        )
        inputs, outputs = token_sum(attempts, "input_tokens"), token_sum(attempts, "output_tokens")
        if not attempts:
            inputs = outputs = 0
        async with asyncio.timeout(5):
            async with SessionFactory() as session:
                session.add(
                    RoutingTrace(
                        agent_run_id=self.run_id,
                        user_id=self.user_id,
                        phase=trace["phase"],
                        mode=trace["mode"],
                        provider=trace["provider"],
                        status=trace["status"],
                        model_version=trace.get("model_version"),
                        selected_id=trace.get("selected_id"),
                        fallback_reason=trace.get("fallback_reason"),
                        latency_ms=trace["latency_ms"],
                        input_tokens=inputs,
                        output_tokens=outputs,
                        provider_calls=len(attempts),
                        reserved_tokens=sum(a.get("reserved_tokens", 0) for a in attempts),
                        details=trace,
                    )
                )
                await session.commit()
        if self.token_recorder and (inputs or outputs):
            await self.token_recorder((inputs or 0) + (outputs or 0))

    async def record_route(self, decision, *, resumed=False):
        await self.record_trace(
            {
                "phase": "query",
                "mode": "shadow" if self.shadow else "active",
                "provider": decision.provider,
                "status": "success",
                "selected_id": decision.route,
                "model_version": decision.model_version,
                "latency_ms": decision.latency_ms,
                "policy_version": decision.policy_version,
                "catalog_version": decision.catalog_version,
                "fallback_reason": decision.fallback_reason,
                "reason_code": decision.reason_code,
                "resumed": resumed,
            }
        )

    async def record_choice(self, choice, *, baseline_tools=(), baseline_skill=None):
        await self.record_trace(
            {
                "phase": "selection",
                "mode": "shadow" if self.shadow else "active",
                "provider": "policy",
                "status": "success",
                "latency_ms": 0,
                "selected_id": choice.capability_id,
                "outcome": choice.outcome,
                "reason_code": choice.reason,
                "policy_version": choice.policy_version,
                "catalog_version": choice.catalog_version,
                "step_number": choice.step_number,
                "pages": choice.pages,
                "baseline_tools": sorted(baseline_tools),
                "baseline_skill": baseline_skill,
            }
        )


async def routing_metrics(session, *, days=7, run_id: UUID | None = None, limit=30, mode=None):
    since = datetime.now(UTC) - timedelta(days=days)
    conditions = [RoutingTrace.created_at >= since]
    if run_id:
        conditions.append(RoutingTrace.agent_run_id == run_id)
    if mode:
        conditions.append(RoutingTrace.mode == mode)
    capabilities = [
        *conditions,
        RoutingTrace.phase.in_(("query", "capability")),
        RoutingTrace.provider != "rules",
    ]
    row = (
        await session.execute(
            select(
                func.count(RoutingTrace.id),
                func.sum(RoutingTrace.input_tokens),
                func.sum(RoutingTrace.output_tokens),
                func.avg(RoutingTrace.latency_ms),
                func.percentile_cont(0.95).within_group(RoutingTrace.latency_ms),
                func.count(RoutingTrace.id).filter(RoutingTrace.mode == "shadow"),
                func.count(RoutingTrace.id).filter(
                    RoutingTrace.details["attempts"].contains([{"provider": "llm"}])
                ),
                func.count(RoutingTrace.id).filter(RoutingTrace.status == "safe_stop"),
                func.count(RoutingTrace.id).filter(
                    RoutingTrace.details["usage_complete"].as_boolean().is_(False)
                ),
            ).where(*capabilities)
        )
    ).one()
    recent = (
        await session.scalars(
            select(RoutingTrace)
            .where(*conditions)
            .order_by(RoutingTrace.created_at.desc(), RoutingTrace.id.desc())
            .limit(limit)
        )
    ).all()
    return {
        "window_days": days,
        "decisions": int(row[0]),
        "input_tokens": int(row[1]) if row[1] is not None else None,
        "output_tokens": int(row[2]) if row[2] is not None else None,
        "average_latency_ms": float(row[3]) if row[3] is not None else None,
        "p95_latency_ms": float(row[4]) if row[4] is not None else None,
        "shadow_decisions": int(row[5]),
        "fallback_decisions": int(row[6]),
        "safe_stops": int(row[7]),
        "unknown_usage_decisions": int(row[8]),
        "recent": [
            {
                "id": str(t.id),
                "run_id": str(t.agent_run_id),
                "phase": t.phase,
                "mode": t.mode,
                "provider": t.provider,
                "status": t.status,
                "model_version": t.model_version,
                "selected_id": t.selected_id,
                "latency_ms": t.latency_ms,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
                "fallback_reason": t.fallback_reason,
                "created_at": t.created_at.isoformat(),
                "details": t.details,
            }
            for t in recent
        ],
    }
