"""Verify the Day 81 migration round trip using a disposable Compose database."""

import json
import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text

from backend.core.config import get_settings


def check() -> dict:
    settings = get_settings()
    if settings.app_env != "development" or settings.db_host != "postgres":
        raise RuntimeError("Migration probe requires the isolated Compose dev PostgreSQL")
    database = f"plugin_migration_{uuid4().hex}"
    assert database.startswith("plugin_migration_") and database.isidentifier()
    assert database not in {"postgres", settings.db_name}
    url = settings.migration_database_url
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    scratch = None
    created = False

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env={**os.environ, "DB_NAME": database},
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            # Never print the connection URL or captured exception containing credentials.
            raise RuntimeError(f"Scratch migration {' '.join(args)} failed")

    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        scratch = create_engine(url.set(database=database))
        alembic("upgrade", "20261004_0022")
        sentinel = uuid4()
        with scratch.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id,email,password_hash) "
                    "VALUES (:id,:email,'synthetic-not-a-real-password')"
                ),
                {"id": sentinel, "email": f"{sentinel}@example.com"},
            )
        alembic("upgrade", "20261018_0023")
        with scratch.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO plugins (id,namespace,version,policy_version,manifest_json) "
                    "VALUES ('migration.probe','migration_probe','1.0.0','1.0.0','{}'::jsonb)"
                )
            )
        alembic("upgrade", "head")
        alembic("check")
        alembic("downgrade", "20261018_0023")
        assert "plugin_secrets" not in inspect(scratch).get_table_names()
        with scratch.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM plugins WHERE id='migration.probe'"))
                == 1
            )
        alembic("upgrade", "head")
        with scratch.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM plugin_catalog_state")) == 1
            assert connection.scalar(text("SELECT auth_version FROM plugin_catalog_state")) == 0
        alembic("downgrade", "20261004_0022")
        assert "plugins" not in inspect(scratch).get_table_names()
        with scratch.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM users WHERE id=:id"), {"id": sentinel})
                == 1
            )
        alembic("upgrade", "head")
        alembic("check")
        return {
            "upgrade_from_day75_revision": "20261004_0022",
            "head": "20261019_0025",
            "day83_downgrade_preserves_plugin": True,
            "upgrade_downgrade_reupgrade": True,
            "legacy_user_preserved": True,
            "metadata_drift": False,
            "scratch_database_removed": True,
        }
    finally:
        if scratch is not None:
            scratch.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    print(json.dumps(check()))
