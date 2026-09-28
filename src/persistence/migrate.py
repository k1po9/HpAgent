"""Small, auditable SQL migration runner for the greenfield Phase A schema."""
from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import psycopg


class SchemaVerificationError(RuntimeError):
    """A missing migration source or an applied schema mismatch."""


def _service() -> str:
    return os.getenv("HPAGENT_SERVICE", "unknown")


def get_migrations_dir() -> Path:
    """Resolve the one runtime migration source shared by migrate and gates."""
    configured = os.getenv("HPAGENT_MIGRATIONS_DIR")
    return Path(configured) if configured else Path("/opt/hpagent/migrations")


def _migration_files() -> tuple[Path, list[Path]]:
    root = get_migrations_dir()
    if not root.is_dir():
        raise SchemaVerificationError(
            f"service={_service()} migration_root={root} migration_file_count=0 "
            "reason=migration_directory_missing"
        )
    files = sorted(root.glob("*.sql"))
    if not files:
        raise SchemaVerificationError(
            f"service={_service()} migration_root={root} migration_file_count=0 "
            "reason=migration_directory_empty"
        )
    return root, files


def verify_schema(database: object) -> None:
    """Fail service startup when the applied schema differs from this checkout."""
    root, files = _migration_files()
    expected = {path.name: sha256(path.read_bytes()).hexdigest() for path in files}
    if isinstance(database, str):
        connection = psycopg.connect(database)
        close = True
    else:
        context = cast(Any, database).connection()
        connection = context.__enter__()
        close = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version,checksum FROM hpagent.schema_migrations")
            applied = {row["version"] if isinstance(row, dict) else row[0]:
                       row["checksum"] if isinstance(row, dict) else row[1]
                       for row in cursor.fetchall()}
        if applied != expected:
            missing = sorted(expected.keys() - applied.keys())
            extra = sorted(applied.keys() - expected.keys())
            changed = sorted(key for key in expected.keys() & applied.keys()
                             if expected[key] != applied[key])
            raise SchemaVerificationError(
                f"service={_service()} migration_root={root} migration_file_count={len(files)} "
                f"expected_count={len(expected)} applied_count={len(applied)} "
                f"missing={missing} extra={extra} changed={changed}"
            )
    except psycopg.Error as exc:
        raise SchemaVerificationError(
            f"service={_service()} migration_root={root} migration_file_count={len(files)} "
            "reason=schema_history_unreadable"
        ) from exc
    finally:
        if close:
            connection.close()
        else:
            context.__exit__(None, None, None)


def migrate(database_url: str | None = None) -> None:
    _, files = _migration_files()
    url = database_url or os.environ["APP_DATABASE_URL"]
    with psycopg.connect(url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(4828436630294757441)")
            cursor.execute("CREATE SCHEMA IF NOT EXISTS hpagent")
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS hpagent.schema_migrations "
                "(version text PRIMARY KEY, checksum text, "
                "applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cursor.execute(
                "ALTER TABLE hpagent.schema_migrations "
                "ADD COLUMN IF NOT EXISTS checksum text"
            )
            for migration in files:
                sql = migration.read_text()
                checksum = sha256(sql.encode()).hexdigest()
                cursor.execute(
                    "SELECT checksum FROM hpagent.schema_migrations WHERE version=%s",
                    (migration.name,),
                )
                applied = cursor.fetchone()
                if applied is None:
                    cursor.execute(sql)
                    cursor.execute(
                        "INSERT INTO hpagent.schema_migrations(version,checksum) VALUES (%s,%s)",
                        (migration.name, checksum),
                    )
                elif applied[0] is None:
                    # One-time upgrade for histories created before checksums existed.
                    cursor.execute(
                        "UPDATE hpagent.schema_migrations SET checksum=%s WHERE version=%s",
                        (checksum, migration.name),
                    )
                elif applied[0] != checksum:
                    raise RuntimeError(f"migration checksum mismatch: {migration.name}")
            cursor.execute(
                "ALTER TABLE hpagent.schema_migrations "
                "ALTER COLUMN checksum SET NOT NULL"
            )
        connection.commit()


if __name__ == "__main__":
    migrate()
