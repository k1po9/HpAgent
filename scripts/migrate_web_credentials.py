#!/usr/bin/env python3
"""Import legacy WEB_CREDENTIALS_JSON Argon2 hashes into PostgreSQL."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from account.identity import normalize_web_subject  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import WEB_CREDENTIALS_JSON hashes for existing Web identities"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("MIGRATION_DATABASE_URL"),
        help="Migration/admin PostgreSQL DSN (default MIGRATION_DATABASE_URL)",
    )
    parser.add_argument(
        "--credentials-json",
        default=os.getenv("WEB_CREDENTIALS_JSON", "{}"),
        help="JSON object mapping username to an existing Argon2 hash",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("set MIGRATION_DATABASE_URL or pass --database-url")
    records = json.loads(args.credentials_json)
    if not isinstance(records, dict):
        parser.error("credentials JSON must be an object")

    imported = skipped = missing = 0
    with psycopg.connect(args.database_url, row_factory=dict_row) as connection:
        connection.execute("SET search_path TO hpagent, public")
        for username, password_hash in records.items():
            subject = normalize_web_subject(str(username))
            if not subject or not isinstance(password_hash, str) or not password_hash.startswith(
                "$argon2"
            ):
                parser.error(f"invalid username or Argon2 hash for {username!r}")
            binding = connection.execute(
                "SELECT identity_binding_id FROM identity_bindings "
                "WHERE provider='web' AND normalized_subject_id=%s AND status='active'",
                (subject,),
            ).fetchone()
            if not binding:
                print(f"missing active Web identity: {subject}", file=sys.stderr)
                missing += 1
                continue
            result = connection.execute(
                "INSERT INTO web_credentials(web_credential_id,identity_binding_id,password_hash) "
                "VALUES (%s,%s,%s) ON CONFLICT(identity_binding_id) DO NOTHING",
                (uuid4(), binding["identity_binding_id"], password_hash),
            )
            if result.rowcount:
                imported += 1
            else:
                skipped += 1
        connection.commit()
    print(f"web credentials: imported={imported} existing={skipped} missing={missing}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
