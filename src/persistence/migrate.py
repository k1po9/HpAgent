"""Small, auditable SQL migration runner for the greenfield Phase A schema."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg


def migrate(database_url: str | None = None) -> None:
    url = database_url or os.environ["APP_DATABASE_URL"]
    root = Path(__file__).resolve().parents[2] / "persistence" / "migrations"
    with psycopg.connect(url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS hpagent")
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS hpagent.schema_migrations "
                "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for migration in sorted(root.glob("*.sql")):
                cursor.execute("SELECT 1 FROM hpagent.schema_migrations WHERE version=%s", (migration.name,))
                if cursor.fetchone() is None:
                    cursor.execute(migration.read_text())
                    cursor.execute("INSERT INTO hpagent.schema_migrations(version) VALUES (%s)", (migration.name,))
        connection.commit()


if __name__ == "__main__":
    migrate()
