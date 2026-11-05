"""Report old unreferenced artifact files; --apply deletes only those verified candidates."""

import argparse
import asyncio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--grace-hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if args.grace_hours < 1 or args.limit < 1:
        parser.error("grace-hours and limit must be positive")

    async def run():
        from backend.database import SessionFactory, engine
        from backend.services.artifacts.maintenance import reconcile_orphans
        from backend.services.artifacts.service import get_artifact_service

        try:
            count = await reconcile_orphans(
                SessionFactory,
                get_artifact_service().storage,
                grace_seconds=args.grace_hours * 3600,
                limit=args.limit,
                dry_run=not args.apply,
            )
            print(
                f"{'Deleted' if args.apply else 'Would delete'} {count} "
                "old unreferenced files/scratch directories"
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
