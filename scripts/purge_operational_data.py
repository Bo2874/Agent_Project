"""Apply the configured retention policy to operational data."""

import argparse
import asyncio
import json

from backend.database import SessionFactory, engine
from backend.services.audit_logs import record_audit_event
from backend.services.operational_retention import purge_expired_operational_data


async def run(*, dry_run: bool) -> None:
    try:
        async with SessionFactory() as session:
            report = await purge_expired_operational_data(
                session,
                apply_changes=not dry_run,
            )
            if not dry_run:
                await record_audit_event(
                    session,
                    actor_user_id=None,
                    action="maintenance.retention.run",
                    target_type="operational_data",
                    metadata=report.to_dict(),
                )
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Áp dụng retention cho dữ liệu vận hành.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tính số bản ghi bị ảnh hưởng rồi rollback.",
    )
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
