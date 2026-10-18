"""Create and validate a PostgreSQL custom-format backup."""

import argparse
import json
from pathlib import Path

from backend.services.database_backup import create_database_backup, validate_database_backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup PostgreSQL kèm SHA-256 manifest.")
    parser.add_argument("--output-dir", type=Path, default=Path("backups"))
    args = parser.parse_args()
    artifact = create_database_backup(output_dir=args.output_dir)
    manifest = validate_database_backup(artifact.backup_path)
    print(
        json.dumps(
            {
                "backup": str(artifact.backup_path),
                "manifest": str(artifact.manifest_path),
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "database": manifest["database"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
