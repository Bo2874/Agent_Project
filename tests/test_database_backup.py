from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.core.config import get_settings
from backend.services import database_backup
from backend.services.database_backup import (
    DatabaseBackupError,
    create_database_backup,
    restore_database_backup,
    validate_database_backup,
)


def fake_postgres_run(command: list[str], **_: object) -> object:
    if "--file" in command:
        output_path = Path(command[command.index("--file") + 1])
        output_path.write_bytes(b"valid-custom-backup")
    return object()


def test_creates_manifest_and_detects_backup_tampering(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(database_backup, "find_postgres_tool", lambda name: Path(name))
    monkeypatch.setattr(database_backup, "_run", fake_postgres_run)
    artifact = create_database_backup(
        output_dir=tmp_path,
        now=datetime(2026, 9, 30, 1, 2, 3, tzinfo=UTC),
    )

    manifest = validate_database_backup(artifact.backup_path)

    assert manifest["sha256"] == artifact.sha256
    assert manifest["backup_file"] == artifact.backup_path.name
    artifact.backup_path.write_bytes(b"tampered")
    with pytest.raises(DatabaseBackupError, match="SHA-256"):
        validate_database_backup(artifact.backup_path)


def test_restore_refuses_primary_database_without_explicit_override(tmp_path) -> None:
    settings = get_settings()

    with pytest.raises(DatabaseBackupError, match="database chính"):
        restore_database_backup(
            tmp_path / "missing.dump",
            target_database=settings.db_name,
            confirm_target=settings.db_name,
            settings=settings,
        )


@pytest.mark.parametrize("name", ["-bad", "bad-name", "1bad", "bad name", ""])
def test_restore_rejects_unsafe_database_names(name: str, tmp_path) -> None:
    with pytest.raises(ValueError, match="Tên database"):
        restore_database_backup(
            tmp_path / "missing.dump",
            target_database=name,
            confirm_target=name,
        )
