"""Print metrics for the latest or a selected eval run."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

from backend.database import SessionFactory, engine
from backend.services.evaluation_metrics import load_eval_run_metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tính pass rate, tool success rate và failure category."
    )
    parser.add_argument(
        "run_id",
        nargs="?",
        type=UUID,
        help="Eval run cần xem; bỏ trống để lấy run mới nhất.",
    )
    return parser.parse_args()


async def run(run_id: UUID | None) -> None:
    try:
        async with SessionFactory() as session:
            report = await load_eval_run_metrics(session, run_id)
        if report is None:
            raise SystemExit("Không tìm thấy eval run.")
        print(json.dumps(report.to_payload(), ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    asyncio.run(run(args.run_id))


if __name__ == "__main__":
    main()
