"""Small, auditable SQL migration runner for the greenfield Phase A schema."""
from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

import psycopg


def verify_schema(database: object) -> None:
    """Fail service startup when the applied schema differs from this checkout."""
    root = Path(__file__).resolve().parents[2] / "persistence" / "migrations"
    expected = {path.name: sha256(path.read_bytes()).hexdigest()
                for path in root.glob("*.sql")}
    if isinstance(database, str):
        connection = psycopg.connect(database)
        close = True
    else:
        context = database.connection()
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
            raise RuntimeError("Workspace schema mismatch; run explicit migrations or "
                               f"rebuild the development database. missing={missing}, "
                               f"extra={extra}, changed={changed}")
    except psycopg.Error as exc:
        raise RuntimeError("Workspace schema is missing or unreadable; run explicit "
                           "migrations or rebuild the development database") from exc
    finally:
        if close:
            connection.close()
        else:
            context.__exit__(None, None, None)


def migrate(database_url: str | None = None) -> None:
    url = database_url or os.environ["APP_DATABASE_URL"]
    root = Path(__file__).resolve().parents[2] / "persistence" / "migrations"
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
            for migration in sorted(root.glob("*.sql")):
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
