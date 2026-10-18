"""Restore a validated PostgreSQL backup into an explicitly confirmed database."""

import argparse
import json
from pathlib import Path

from backend.services.database_backup import database_counts, restore_database_backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore PostgreSQL backup an toàn.")
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--target-db", required=True)
    parser.add_argument("--confirm-target", required=True)
    parser.add_argument("--replace-target", action="store_true")
    parser.add_argument("--allow-primary", action="store_true")
    args = parser.parse_args()
    manifest = restore_database_backup(
        args.backup,
        target_database=args.target_db,
        confirm_target=args.confirm_target,
        replace_target=args.replace_target,
        allow_primary=args.allow_primary,
    )
    print(
        json.dumps(
            {
                "restored": args.target_db,
                "source_database": manifest["database"],
                "counts": database_counts(args.target_db),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
