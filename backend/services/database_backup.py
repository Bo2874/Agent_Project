from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from backend.core.config import PROJECT_ROOT, Settings, get_settings
from backend.core.redaction import redact_text

DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
BACKUP_FORMAT_VERSION = 1
COUNTED_TABLES = (
    "users",
    "conversations",
    "messages",
    "agent_runs",
    "tool_calls",
    "audit_logs",
)


class DatabaseBackupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    backup_path: Path
    manifest_path: Path
    sha256: str
    size_bytes: int


def _validate_database_name(value: str) -> str:
    if not DATABASE_NAME_PATTERN.fullmatch(value):
        raise ValueError("Tên database chỉ được gồm chữ, số, dấu gạch dưới và bắt đầu bằng chữ")
    return value


def find_postgres_tool(name: str) -> Path:
    executable = f"{name}.exe" if os.name == "nt" else name
    discovered = shutil.which(executable) or shutil.which(name)
    if discovered:
        return Path(discovered)
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        for version in range(20, 12, -1):
            candidate = program_files / "PostgreSQL" / str(version) / "bin" / executable
            if candidate.is_file():
                return candidate
    raise DatabaseBackupError(f"Không tìm thấy PostgreSQL tool: {name}")


def _postgres_env(settings: Settings) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PGPASSWORD"] = settings.db_password.get_secret_value()
    return environment


def _connection_args(settings: Settings, database: str | None = None) -> list[str]:
    return [
        "--host",
        settings.db_host,
        "--port",
        str(settings.db_port),
        "--username",
        settings.db_user,
        "--dbname",
        database or settings.db_name,
    ]


def _run(command: list[str], *, settings: Settings) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        env=_postgres_env(settings),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        detail = redact_text(result.stderr or result.stdout or "Không có chi tiết", max_length=1000)
        raise DatabaseBackupError(f"PostgreSQL command thất bại: {detail}")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_database_backup(
    *,
    output_dir: Path | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> BackupArtifact:
    active_settings = settings or get_settings()
    destination = (output_dir or PROJECT_ROOT / "backups").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    backup_path = destination / f"{active_settings.db_name}_{timestamp}.dump"
    if backup_path.exists():
        raise DatabaseBackupError(f"Backup đã tồn tại: {backup_path.name}")

    command = [
        str(find_postgres_tool("pg_dump")),
        *_connection_args(active_settings),
        "--format=custom",
        "--compress=6",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(backup_path),
    ]
    try:
        _run(command, settings=active_settings)
    except BaseException:
        if backup_path.exists():
            backup_path.unlink()
        raise

    digest = _sha256(backup_path)
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".json")
    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "database": active_settings.db_name,
        "created_at": (now or datetime.now(UTC)).isoformat(),
        "backup_file": backup_path.name,
        "sha256": digest,
        "size_bytes": backup_path.stat().st_size,
        "pg_format": "custom",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return BackupArtifact(
        backup_path=backup_path,
        manifest_path=manifest_path,
        sha256=digest,
        size_bytes=backup_path.stat().st_size,
    )


def validate_database_backup(
    backup_path: Path,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    active_settings = settings or get_settings()
    resolved = backup_path.resolve()
    if not resolved.is_file():
        raise DatabaseBackupError("Không tìm thấy file backup")
    manifest_path = resolved.with_suffix(resolved.suffix + ".json")
    if not manifest_path.is_file():
        raise DatabaseBackupError("Backup thiếu manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise DatabaseBackupError("Manifest backup không tương thích")
    if manifest.get("backup_file") != resolved.name:
        raise DatabaseBackupError("Tên file backup không khớp manifest")
    digest = _sha256(resolved)
    if manifest.get("sha256") != digest:
        raise DatabaseBackupError("SHA-256 backup không khớp manifest")
    _run(
        [str(find_postgres_tool("pg_restore")), "--list", str(resolved)],
        settings=active_settings,
    )
    return manifest


def restore_database_backup(
    backup_path: Path,
    *,
    target_database: str,
    confirm_target: str,
    replace_target: bool = False,
    allow_primary: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    active_settings = settings or get_settings()
    target = _validate_database_name(target_database)
    if confirm_target != target:
        raise DatabaseBackupError("Tên xác nhận không khớp target database")
    if target == active_settings.db_name and not allow_primary:
        raise DatabaseBackupError("Từ chối restore vào database chính nếu thiếu --allow-primary")
    manifest = validate_database_backup(backup_path, settings=active_settings)

    common = [
        "--host",
        active_settings.db_host,
        "--port",
        str(active_settings.db_port),
        "--username",
        active_settings.db_user,
    ]
    if replace_target:
        _run(
            [
                str(find_postgres_tool("dropdb")),
                *common,
                "--maintenance-db=postgres",
                "--if-exists",
                "--force",
                target,
            ],
            settings=active_settings,
        )
    _run(
        [
            str(find_postgres_tool("createdb")),
            *common,
            "--maintenance-db=postgres",
            target,
        ],
        settings=active_settings,
    )
    _run(
        [
            str(find_postgres_tool("pg_restore")),
            *_connection_args(active_settings, target),
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            str(backup_path.resolve()),
        ],
        settings=active_settings,
    )
    return manifest


def database_counts(
    database: str,
    *,
    settings: Settings | None = None,
) -> dict[str, int | str]:
    active_settings = settings or get_settings()
    target = _validate_database_name(database)
    with psycopg.connect(
        host=active_settings.db_host,
        port=active_settings.db_port,
        dbname=target,
        user=active_settings.db_user,
        password=active_settings.db_password.get_secret_value(),
    ) as connection:
        with connection.cursor() as cursor:
            counts: dict[str, int | str] = {}
            for table in COUNTED_TABLES:
                cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                row = cursor.fetchone()
                counts[table] = int(row[0]) if row else 0
            cursor.execute("SELECT version_num FROM alembic_version")
            version_row = cursor.fetchone()
            counts["alembic_version"] = str(version_row[0]) if version_row else "missing"
            return counts


def drop_restored_database(
    target_database: str,
    *,
    settings: Settings | None = None,
) -> None:
    active_settings = settings or get_settings()
    target = _validate_database_name(target_database)
    if target == active_settings.db_name:
        raise DatabaseBackupError("Không được dùng cleanup để xóa database chính")
    common = [
        "--host",
        active_settings.db_host,
        "--port",
        str(active_settings.db_port),
        "--username",
        active_settings.db_user,
    ]
    _run(
        [
            str(find_postgres_tool("dropdb")),
            *common,
            "--maintenance-db=postgres",
            "--if-exists",
            "--force",
            target,
        ],
        settings=active_settings,
    )
