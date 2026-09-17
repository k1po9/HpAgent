"""Small, auditable SQL migration runner for the greenfield Phase A schema."""
from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

import psycopg


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
