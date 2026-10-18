"""Backup, restore into a temporary database, compare counts, then clean up."""

import json
import tempfile
from pathlib import Path
from uuid import uuid4

from backend.core.config import get_settings
from backend.services.database_backup import (
    create_database_backup,
    database_counts,
    drop_restored_database,
    restore_database_backup,
)


def main() -> None:
    settings = get_settings()
    target = f"personal_agent_restore_verify_{uuid4().hex[:8]}"
    source_counts = database_counts(settings.db_name, settings=settings)
    try:
        with tempfile.TemporaryDirectory(prefix="personal-agent-backup-") as directory:
            artifact = create_database_backup(
                output_dir=Path(directory),
                settings=settings,
            )
            restore_database_backup(
                artifact.backup_path,
                target_database=target,
                confirm_target=target,
                settings=settings,
            )
            restored_counts = database_counts(target, settings=settings)
            if restored_counts != source_counts:
                raise RuntimeError(
                    f"Số bản ghi sau restore không khớp: {restored_counts} != {source_counts}"
                )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "temporary_database": target,
                        "sha256": artifact.sha256,
                        "counts": restored_counts,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        drop_restored_database(target, settings=settings)


if __name__ == "__main__":
    main()
