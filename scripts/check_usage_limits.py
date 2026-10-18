"""Smoke-test PostgreSQL rate limits and daily quotas."""

import asyncio
from uuid import uuid4

from sqlalchemy import delete

from agent_core.graph import AgentQuotaLimitError
from backend.database import SessionFactory, engine
from backend.models import User
from backend.services.usage_limits import (
    DAY_SECONDS,
    MINUTE_SECONDS,
    AgentQuotaGuard,
    UsageLimit,
    UsageLimitExceeded,
    add_usage,
    consume_usage_limits,
    get_current_usage,
)


async def run_check() -> int:
    user_id = uuid4()
    try:
        async with SessionFactory() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"usage-{uuid4().hex}@example.test",
                    password_hash="not-used-by-smoke-test",
                    role="user",
                )
            )
            await session.commit()

        request_limit = UsageLimit(
            metric="agent_request",
            limit=2,
            window_seconds=MINUTE_SECONDS,
        )
        await consume_usage_limits(user_id, (request_limit,))
        await consume_usage_limits(user_id, (request_limit,))
        try:
            await consume_usage_limits(user_id, (request_limit,))
        except UsageLimitExceeded:
            rate_limit_blocked = True
        else:
            rate_limit_blocked = False

        run_limit = UsageLimit(metric="agent_run", limit=1, window_seconds=DAY_SECONDS)
        await consume_usage_limits(user_id, (run_limit,))
        try:
            await consume_usage_limits(
                user_id,
                (
                    UsageLimit(metric="tool_call", limit=10, window_seconds=DAY_SECONDS),
                    run_limit,
                ),
            )
        except UsageLimitExceeded:
            bundle_blocked = True
        else:
            bundle_blocked = False
        tool_usage_after_rollback = await get_current_usage(
            user_id,
            metric="tool_call",
            window_seconds=DAY_SECONDS,
        )

        await add_usage(user_id, metric="llm_token", amount=42)
        token_usage = await get_current_usage(
            user_id,
            metric="llm_token",
            window_seconds=DAY_SECONDS,
        )
        guard = AgentQuotaGuard(user_id=user_id, token_limit=42, tool_call_limit=10)
        try:
            await guard.before_execution("model")
        except AgentQuotaLimitError:
            token_quota_blocked = True
        else:
            token_quota_blocked = False

        checks = {
            "rate_limit_blocked": rate_limit_blocked,
            "multi_limit_transaction_rollback": (bundle_blocked and tool_usage_after_rollback == 0),
            "token_usage_recorded": token_usage == 42,
            "token_quota_blocked": token_quota_blocked,
        }
        for name, passed in checks.items():
            print(f"{name}: {'PASSED' if passed else 'FAILED'}")
        all_passed = all(checks.values())
        print("USAGE_LIMITS_PASSED" if all_passed else "USAGE_LIMITS_FAILED")
        return 0 if all_passed else 1
    finally:
        async with SessionFactory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()


def main() -> None:
    raise SystemExit(asyncio.run(run_check()))


if __name__ == "__main__":
    main()
