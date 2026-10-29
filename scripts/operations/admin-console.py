#!/usr/bin/env python3
"""Local admin page for invites, users, Docker logs, and SQL.

Bind to loopback, start it when needed, and stop it with Ctrl-C. It is not a
product API and must not be exposed beyond the operator's machine.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

APP_HOST = "127.0.0.1"
APP_PORT = 5434
APP_DATABASE = "hpagent"
ROW_LIMIT = 500
DETAIL_CAP = 200
STATEMENT_TIMEOUT = "30s"
LOCK_TIMEOUT = "5s"
LOG_TIMEOUT_SECONDS = 30
PSQL_TIMEOUT_SECONDS = 35
LOG_OUTPUT_LIMIT = 1_000_000
PSQL_OUTPUT_LIMIT = 512_000
SQL_LIMIT = 200_000
BODY_LIMIT = 1_000_000
TAIL_CHOICES = (100, 200, 500, 1000)
PASSWORD_ENVS = (
    "HPAGENT_MIGRATE_PASSWORD",
    "HPAGENT_API_PASSWORD",
    "HPAGENT_WORKER_PASSWORD",
    "TEMPORAL_DB_PASSWORD",
    "HINDSIGHT_DB_PASSWORD",
)
COMPOSE_PREFIX = (
    "docker",
    "compose",
    "--profile",
    "agent",
    "--profile",
    "web",
    "--profile",
    "web-prod",
    "--profile",
    "qq",
    "--profile",
    "tools",
)
LOG_GROUPS = {
    "all": (),
    "api": ("hpagent-api",),
    "worker": ("hpagent", "hpagent-document-worker"),
    "qq": ("hpagent", "snowluma"),
    "infra": (
        "app-postgres",
        "redis",
        "temporal",
        "temporal-postgres",
        "hindsight",
        "hindsight-postgres",
        "searxng",
        "gotenberg",
    ),
    "web-dev": ("web-dev",),
    "web-gateway": ("web-gateway",),
    "hpagent-migrate": ("hpagent-migrate",),
    "temporal-web": ("temporal-web",),
}

USER_LIST_SQL = """
SELECT a.account_id, a.status, a.created_at,
       e.model_access_tier, e.daily_token_limit, e.prompt_visibility, e.expires_at,
       web.external_subject_id AS username,
       qq.external_subject_id AS qq_subject,
       qq.metadata->>'channel_type' AS qq_channel,
       bud.used_tokens, bud.reserved_tokens
FROM accounts a
LEFT JOIN account_entitlements e ON e.account_id = a.account_id
LEFT JOIN LATERAL (
  SELECT external_subject_id FROM identity_bindings
  WHERE account_id = a.account_id AND provider = 'web' AND status = 'active'
  ORDER BY created_at DESC LIMIT 1
) web ON true
LEFT JOIN LATERAL (
  SELECT external_subject_id, metadata FROM identity_bindings
  WHERE account_id = a.account_id AND provider = 'qq' AND status = 'active'
  ORDER BY created_at DESC LIMIT 1
) qq ON true
LEFT JOIN account_daily_model_budgets bud
  ON bud.account_id = a.account_id AND bud.quota_date = (now() AT TIME ZONE 'utc')::date
ORDER BY a.created_at DESC
""".strip()

INVITE_LIST_SQL = """
SELECT invite_id, entitlement_profile, max_redemptions, redemption_count,
       expires_at, revoked_at, created_at
FROM registration_invites
ORDER BY created_at DESC
""".strip()

TABLE_LIST_SQL = """
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name
""".strip()

ACCOUNT_SQL = """
SELECT account_id, status, version, memory_epoch, created_at, updated_at
FROM accounts WHERE account_id = %s
""".strip()

BINDINGS_SQL = """
SELECT identity_binding_id, provider, external_subject_id, normalized_subject_id,
       status, verified_at, revoked_at, metadata, version, created_at, updated_at
FROM identity_bindings WHERE account_id = %s
ORDER BY created_at
""".strip()

ENTITLEMENT_SQL = """
SELECT model_access_tier, daily_token_limit, prompt_visibility, expires_at,
       version, provisioned_by_invite_id, created_at, updated_at
FROM account_entitlements WHERE account_id = %s
""".strip()

INVITE_SOURCE_SQL = """
SELECT i.invite_id, i.entitlement_profile, i.max_redemptions, i.redemption_count,
       i.expires_at, i.revoked_at, i.created_at
FROM account_entitlements e
JOIN registration_invites i ON i.invite_id = e.provisioned_by_invite_id
WHERE e.account_id = %s
""".strip()

CREDENTIALS_SQL = """
SELECT c.created_at, c.updated_at
FROM web_credentials AS c
JOIN identity_bindings AS b ON b.identity_binding_id = c.identity_binding_id
WHERE b.account_id = %s
ORDER BY c.created_at
""".strip()

SESSIONS_SQL = """
SELECT web_auth_session_id, expires_at, idle_expires_at, last_seen_at, revoked_at, created_at
FROM web_auth_sessions WHERE account_id = %s
ORDER BY created_at DESC
LIMIT %s
""".strip()

BUDGETS_SQL = """
SELECT quota_date, used_tokens, reserved_tokens, updated_at
FROM account_daily_model_budgets
WHERE account_id = %s
  AND quota_date >= ((now() AT TIME ZONE 'utc')::date - 6)
ORDER BY quota_date DESC
""".strip()

CHALLENGES_SQL = """
SELECT challenge_id, provider, status, expires_at, verified_at, consumed_at, cancelled_at, created_at
FROM identity_binding_challenges WHERE account_id = %s
ORDER BY created_at DESC
LIMIT %s
""".strip()

WORKSPACE_SQL = """
SELECT workspace_id, created_at FROM account_workspaces WHERE account_id = %s
""".strip()

COUNTS_SQL = """
SELECT
  (SELECT count(*) FROM conversations WHERE account_id = %s) AS conversations,
  (SELECT count(*) FROM messages WHERE account_id = %s) AS messages,
  (SELECT count(*) FROM works WHERE account_id = %s) AS works
""".strip()

CONVERSATIONS_SQL = """
SELECT conversation_id, title, status, updated_at
FROM conversations WHERE account_id = %s
ORDER BY updated_at DESC
LIMIT %s
""".strip()

WORKS_SQL = """
SELECT work_id, title, status, updated_at
FROM works WHERE account_id = %s
ORDER BY updated_at DESC
LIMIT %s
""".strip()

FIXED_SQL_STATEMENTS = (
    USER_LIST_SQL,
    INVITE_LIST_SQL,
    TABLE_LIST_SQL,
    ACCOUNT_SQL,
    BINDINGS_SQL,
    ENTITLEMENT_SQL,
    INVITE_SOURCE_SQL,
    CREDENTIALS_SQL,
    SESSIONS_SQL,
    BUDGETS_SQL,
    CHALLENGES_SQL,
    WORKSPACE_SQL,
    COUNTS_SQL,
    CONVERSATIONS_SQL,
    WORKS_SQL,
)
SQL_PRESETS = (
    ("users", "用户", USER_LIST_SQL),
    ("invites", "邀请码", INVITE_LIST_SQL),
    ("tables", "表清单", TABLE_LIST_SQL),
)


class SqlTarget:
    def __init__(
        self,
        key: str,
        label: str,
        kind: str,
        user: str,
        password_env: str,
        database: str,
        *,
        search_path: bool = False,
        service: str | None = None,
        database_env: str | None = None,
    ) -> None:
        self.key = key
        self.label = label
        self.kind = kind
        self.user = user
        self.password_env = password_env
        self.database = database
        self.search_path = search_path
        self.service = service
        self.database_env = database_env


SQL_TARGETS = (
    SqlTarget(
        "app-migrate", "应用库 / 迁移角色", "tcp", "hpagent_migrate",
        "HPAGENT_MIGRATE_PASSWORD", APP_DATABASE, search_path=True,
    ),
    SqlTarget(
        "app-api", "应用库 / API 角色", "tcp", "hpagent_api",
        "HPAGENT_API_PASSWORD", APP_DATABASE, search_path=True,
    ),
    SqlTarget(
        "app-worker", "应用库 / Worker 角色", "tcp", "hpagent_worker",
        "HPAGENT_WORKER_PASSWORD", APP_DATABASE, search_path=True,
    ),
    SqlTarget(
        "temporal", "Temporal", "docker", "temporal",
        "TEMPORAL_DB_PASSWORD", "temporal", service="temporal-postgres",
    ),
    SqlTarget(
        "hindsight", "Hindsight", "docker", "hindsight",
        "HINDSIGHT_DB_PASSWORD", "hindsight",
        service="hindsight-postgres", database_env="HINDSIGHT_DB_NAME",
    ),
)
SQL_TARGET_BY_KEY = {item.key: item for item in SQL_TARGETS}


class ConsoleError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes | memoryview):
        return bytes(value).hex()
    return str(value)


def dumps(body: Any) -> str:
    return json.dumps(body, ensure_ascii=False, default=json_default)


def redact(text: str) -> str:
    redacted = text
    for name in PASSWORD_ENVS:
        value = os.environ.get(name)
        if value:
            redacted = redacted.replace(value, "***")
    return redacted


def db_message(exc: BaseException) -> str:
    diag = getattr(exc, "diag", None)
    primary = getattr(diag, "message_primary", None) if diag else None
    return redact(primary or str(exc))


def require_password(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ConsoleError(f"{name} is not set")
    return value


def positive_int(value: Any, name: str, *, allow_null: bool) -> int | None:
    if value is None or value == "":
        if allow_null:
            return None
        raise ConsoleError(f"{name} must be a positive integer")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConsoleError(f"{name} must be a positive integer")
    return value


def optional_timestamp(value: Any, name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConsoleError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConsoleError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ConsoleError(f"{name} must include a timezone")
    return parsed


def invite_request(body: dict[str, Any]) -> tuple[Any, int, datetime | None]:
    from account.invite_service import EntitlementProfile

    if not isinstance(body, dict):
        raise ConsoleError("JSON object required")
    daily = positive_int(body.get("daily_token_limit"), "daily_token_limit", allow_null=True)
    visibility = body.get("prompt_visibility") or "none"
    if visibility not in {"none", "summary", "full_safe"}:
        raise ConsoleError("prompt_visibility must be none, summary, or full_safe")
    if "max_redemptions" not in body or body.get("max_redemptions") == "":
        max_redemptions = 1
    else:
        parsed = positive_int(body.get("max_redemptions"), "max_redemptions", allow_null=False)
        max_redemptions = 1 if parsed is None else parsed
    invite_expires = optional_timestamp(body.get("invite_expires_at"), "invite_expires_at")
    entitlement_expires = optional_timestamp(
        body.get("entitlement_expires_at"), "entitlement_expires_at"
    )
    profile = EntitlementProfile("standard", daily, visibility, entitlement_expires)
    return profile, max_redemptions, invite_expires


def migrate_conninfo() -> str:
    return make_conninfo(
        host=APP_HOST,
        port=APP_PORT,
        dbname=APP_DATABASE,
        user="hpagent_migrate",
        password=require_password("HPAGENT_MIGRATE_PASSWORD"),
        application_name="hpagent-admin-console",
    )


def create_invite(body: dict[str, Any]) -> dict[str, str]:
    from account.invite_service import RegistrationInviteService

    profile, max_redemptions, invite_expires = invite_request(body)
    try:
        created = RegistrationInviteService(migrate_conninfo()).create(
            profile,
            max_redemptions=max_redemptions,
            expires_at=invite_expires,
        )
    except psycopg.Error as exc:
        raise ConsoleError(db_message(exc), HTTPStatus.BAD_GATEWAY) from exc
    except ValueError as exc:
        raise ConsoleError(redact(str(exc))) from exc
    return {"invite_id": str(created.invite_id), "invite_code": created.code}


def connect_app(user: str, password_env: str) -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(
        host=APP_HOST,
        port=APP_PORT,
        dbname=APP_DATABASE,
        user=user,
        password=require_password(password_env),
        connect_timeout=2,
        application_name="hpagent-admin-console",
        row_factory=dict_row,
    )


def prepare_session(conn: psycopg.Connection[Any], *, read_only: bool, search_path: bool) -> None:
    if read_only:
        conn.execute("SET TRANSACTION READ ONLY")
    if search_path:
        conn.execute("SET search_path TO hpagent, public")
    conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
    conn.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")


def fetch_all(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return list(conn.execute(sql, params).fetchall())


def fetch_one(
    conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...]
) -> dict[str, Any] | None:
    return conn.execute(sql, params).fetchone()


def fetch_capped(
    conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...], cap: int = DETAIL_CAP
) -> tuple[list[dict[str, Any]], bool]:
    rows = fetch_all(conn, sql, (*params, cap + 1))
    return rows[:cap], len(rows) > cap


def parse_account_id(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        raise ConsoleError("account_id must be a UUID") from exc


def list_invites() -> dict[str, Any]:
    return {"invites": _read_migrate(INVITE_LIST_SQL)}


def list_users() -> dict[str, Any]:
    return {"users": _read_migrate(USER_LIST_SQL)}


def _read_migrate(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = connect_app("hpagent_migrate", "HPAGENT_MIGRATE_PASSWORD")
    try:
        prepare_session(conn, read_only=True, search_path=True)
        rows = fetch_all(conn, sql, params)
        conn.commit()
        return rows
    except ConsoleError:
        conn.rollback()
        raise
    except psycopg.Error as exc:
        conn.rollback()
        raise ConsoleError(db_message(exc), HTTPStatus.BAD_GATEWAY) from exc
    finally:
        conn.close()


def user_detail(account_id: str) -> dict[str, Any]:
    account_uuid = parse_account_id(account_id)
    conn = connect_app("hpagent_migrate", "HPAGENT_MIGRATE_PASSWORD")
    try:
        prepare_session(conn, read_only=True, search_path=True)
        account = fetch_one(conn, ACCOUNT_SQL, (account_uuid,))
        if account is None:
            conn.rollback()
            raise ConsoleError("account not found", HTTPStatus.NOT_FOUND)
        sessions, sessions_truncated = fetch_capped(conn, SESSIONS_SQL, (account_uuid,))
        challenges, challenges_truncated = fetch_capped(conn, CHALLENGES_SQL, (account_uuid,))
        conversations, conversations_truncated = fetch_capped(
            conn, CONVERSATIONS_SQL, (account_uuid,)
        )
        works, works_truncated = fetch_capped(conn, WORKS_SQL, (account_uuid,))
        payload = {
            "account": account,
            "bindings": fetch_all(conn, BINDINGS_SQL, (account_uuid,)),
            "entitlement": fetch_one(conn, ENTITLEMENT_SQL, (account_uuid,)),
            "invite": fetch_one(conn, INVITE_SOURCE_SQL, (account_uuid,)),
            "credentials": fetch_all(conn, CREDENTIALS_SQL, (account_uuid,)),
            "sessions": sessions,
            "sessions_truncated": sessions_truncated,
            "budgets": fetch_all(conn, BUDGETS_SQL, (account_uuid,)),
            "challenges": challenges,
            "challenges_truncated": challenges_truncated,
            "workspace": fetch_one(conn, WORKSPACE_SQL, (account_uuid,)),
            "counts": fetch_one(conn, COUNTS_SQL, (account_uuid, account_uuid, account_uuid)),
            "conversations": conversations,
            "conversations_truncated": conversations_truncated,
            "works": works,
            "works_truncated": works_truncated,
        }
        conn.commit()
        return payload
    except psycopg.Error as exc:
        conn.rollback()
        raise ConsoleError(db_message(exc), HTTPStatus.BAD_GATEWAY) from exc
    finally:
        conn.close()


def normalize_sql(sql: Any) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise ConsoleError("SQL is required")
    if len(sql) > SQL_LIMIT:
        raise ConsoleError("SQL is too long")
    return sql.strip()


def target_database(target: SqlTarget) -> str:
    if target.database_env:
        configured = os.environ.get(target.database_env, "").strip()
        if configured:
            return configured
    return target.database


def log_argv(group: str, tail: int) -> list[str]:
    if group not in LOG_GROUPS:
        raise ConsoleError("unknown log group")
    if tail not in TAIL_CHOICES:
        raise ConsoleError("tail must be 100, 200, 500, or 1000")
    return [*COMPOSE_PREFIX, "logs", "--tail", str(tail), *LOG_GROUPS[group]]


def psql_argv(service: str, user: str, database: str) -> list[str]:
    script = (
        "IFS= read -r PGPASSWORD; export PGPASSWORD; "
        'exec psql -h 127.0.0.1 -U "$1" -d "$2" -v ON_ERROR_STOP=1 -P pager=off'
    )
    return [*COMPOSE_PREFIX, "exec", "-T", service, "sh", "-c", script, "sh", user, database]


def psql_stdin(password: str, sql: str) -> bytes:
    if "\n" in password or "\r" in password:
        raise ConsoleError("database password must be a single line")
    script = (
        f"SET statement_timeout = '{STATEMENT_TIMEOUT}';\n"
        f"SET lock_timeout = '{LOCK_TIMEOUT}';\n"
        f"{sql}\n"
    )
    return f"{password}\n{script}".encode()


def _run(argv: list[str], *, stdin: bytes | None, timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConsoleError("command timed out", HTTPStatus.BAD_GATEWAY) from exc
    except FileNotFoundError as exc:
        raise ConsoleError("docker not found", HTTPStatus.BAD_GATEWAY) from exc


def _output(completed: subprocess.CompletedProcess[bytes]) -> tuple[str, str]:
    stdout = redact(completed.stdout.decode("utf-8", "replace"))
    stderr = redact(completed.stderr.decode("utf-8", "replace"))
    return stdout, stderr


def fetch_logs(group: str, tail: int) -> dict[str, Any]:
    completed = _run(log_argv(group, tail), stdin=None, timeout=LOG_TIMEOUT_SECONDS, env=os.environ.copy())
    stdout, stderr = _output(completed)
    if completed.returncode != 0:
        raise ConsoleError(stderr or stdout or "docker compose logs failed", HTTPStatus.BAD_GATEWAY)
    text = stdout
    if stderr.strip():
        text = f"{stdout}\n{stderr}" if stdout else stderr
    truncated = len(text) > LOG_OUTPUT_LIMIT
    return {"text": text[:LOG_OUTPUT_LIMIT], "truncated": truncated}


def execute_statement(conn: psycopg.Connection[Any], sql: str) -> dict[str, Any]:
    # Execute once: SQL starting with WITH/EXPLAIN can also have side effects.
    # A regular cursor supports DML RETURNING and multiple result sets without
    # a speculative DECLARE followed by rollback/retry (which loses SETs).
    results: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(sql)
        while True:
            if cur.description is not None:
                fetched = cur.fetchmany(ROW_LIMIT + 1)
                results.append({
                    "kind": "rows",
                    "columns": [item.name for item in cur.description],
                    "rows": fetched[:ROW_LIMIT],
                    "truncated": len(fetched) > ROW_LIMIT,
                    "rowcount": cur.rowcount,
                    "status": cur.statusmessage,
                })
            else:
                results.append({
                    "kind": "command",
                    "rowcount": cur.rowcount,
                    "status": cur.statusmessage,
                })
            if not cur.nextset():
                break
    if len(results) == 1:
        return results[0]
    return {"kind": "batch", "results": results}


def execute_tcp(target: SqlTarget, sql: str) -> dict[str, Any]:
    conn = connect_app(target.user, target.password_env)
    try:
        prepare_session(conn, read_only=False, search_path=target.search_path)
        result = execute_statement(conn, sql)
        conn.commit()
        return result
    except ConsoleError:
        conn.rollback()
        raise
    except psycopg.Error as exc:
        conn.rollback()
        raise ConsoleError(db_message(exc), HTTPStatus.BAD_GATEWAY) from exc
    finally:
        conn.close()


def execute_docker(target: SqlTarget, sql: str) -> dict[str, Any]:
    if not target.service:
        raise ConsoleError("docker SQL target is missing a service")
    password = require_password(target.password_env)
    completed = _run(
        psql_argv(target.service, target.user, target_database(target)),
        stdin=psql_stdin(password, sql),
        timeout=PSQL_TIMEOUT_SECONDS,
        env=os.environ.copy(),
    )
    stdout, stderr = _output(completed)
    if completed.returncode != 0:
        raise ConsoleError(stderr or stdout or f"psql exited {completed.returncode}", HTTPStatus.BAD_GATEWAY)
    text = stdout
    if stderr.strip():
        text = f"{stdout}\n{stderr}" if stdout else stderr
    truncated = len(text) > PSQL_OUTPUT_LIMIT
    return {"kind": "text", "text": text[:PSQL_OUTPUT_LIMIT], "truncated": truncated}


def execute_sql(target_key: Any, sql: Any) -> dict[str, Any]:
    if not isinstance(target_key, str) or target_key not in SQL_TARGET_BY_KEY:
        raise ConsoleError("unknown SQL target")
    statement = normalize_sql(sql)
    target = SQL_TARGET_BY_KEY[target_key]
    if target.kind == "tcp":
        return execute_tcp(target, statement)
    return execute_docker(target, statement)


def public_targets() -> dict[str, Any]:
    return {
        "targets": [
            {"id": item.key, "label": item.label, "kind": item.kind} for item in SQL_TARGETS
        ]
    }


def public_presets() -> dict[str, Any]:
    return {
        "presets": [{"id": key, "label": label, "sql": sql} for key, label, sql in SQL_PRESETS]
    }


def read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    if handler.headers.get_content_type() != "application/json":
        raise ConsoleError("Content-Type must be application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    lengths = handler.headers.get_all("Content-Length", [])
    if len(lengths) != 1 or handler.headers.get("Transfer-Encoding"):
        raise ConsoleError("one Content-Length is required")
    try:
        length = int(lengths[0])
    except ValueError as exc:
        raise ConsoleError("Content-Length must be an integer") from exc
    if length < 0 or length > BODY_LIMIT:
        raise ConsoleError("body is too large")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        raise ConsoleError("JSON body required")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConsoleError("JSON body is invalid") from exc
    if not isinstance(body, dict):
        raise ConsoleError("JSON object required")
    return body


def query_int(params: dict[str, list[str]], name: str, default: int) -> int:
    values = params.get(name)
    if not values or values[0] == "":
        return default
    try:
        return int(values[0])
    except ValueError as exc:
        raise ConsoleError(f"{name} must be an integer") from exc


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HpAgent 管理</title>
<style>
body { margin: 0; color: #1c1917; background: #fafaf9; font: 14px/1.45 sans-serif; }
header { display: flex; gap: 16px; align-items: center; padding: 12px 16px; background: #fff; border-bottom: 1px solid #e7e5e4; }
h1 { font-size: 18px; margin: 0; }
nav { display: flex; gap: 8px; }
button, select, input, textarea { font: inherit; }
nav button, button { border: 1px solid #d6d3d1; background: #fff; border-radius: 6px; padding: 6px 10px; cursor: pointer; }
nav button.on { background: #1c1917; color: #fff; border-color: #1c1917; }
main { padding: 16px; display: grid; gap: 16px; }
label { display: grid; gap: 4px; margin: 8px 0; }
input, select, textarea { padding: 6px 8px; border: 1px solid #d6d3d1; border-radius: 6px; }
textarea { width: 100%; min-height: 180px; box-sizing: border-box; }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
.grow { flex: 1; min-width: 220px; }
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { border: 1px solid #e7e5e4; padding: 6px 8px; text-align: left; vertical-align: top; }
.wrap { overflow: auto; max-height: 70vh; background: #fff; }
pre { white-space: pre-wrap; background: #1c1917; color: #fafaf9; padding: 12px; max-height: 70vh; overflow: auto; }
.hidden { display: none; }
#status { min-height: 1.2em; margin: 0; }
.error { color: #b91c1c; }
.ok { background: #ecfdf5; border: 1px solid #a7f3d0; padding: 12px; display: grid; gap: 8px; }
.click { cursor: pointer; }
.muted { color: #57534e; }
h2 { font-size: 16px; margin: 16px 0 8px; }
</style>
</head>
<body>
<header>
  <h1>HpAgent 管理</h1>
  <nav>
    <button type="button" data-tab="invites" class="on">邀请码</button>
    <button type="button" data-tab="users">用户</button>
    <button type="button" data-tab="logs">Docker 日志</button>
    <button type="button" data-tab="sql">SQL</button>
  </nav>
</header>
<main>
  <p id="status"></p>
  <section id="invites">
    <p class="muted">使用迁移角色连接 127.0.0.1:5434。明文邀请码只在创建成功时出现一次。</p>
    <div class="row">
      <button type="button" data-limit="5000">A · 5000</button>
      <button type="button" data-limit="50000">B · 50000</button>
      <button type="button" data-limit="100000">C · 100000</button>
    </div>
    <form id="invite-form">
      <div class="row">
        <label class="grow">日额度<input id="daily" inputmode="numeric" placeholder="空表示不设上限"></label>
        <label class="grow">Prompt 可见性
          <select id="visibility">
            <option value="none">none</option>
            <option value="summary">summary</option>
            <option value="full_safe">full_safe</option>
          </select>
        </label>
        <label class="grow">可兑换次数<input id="redemptions" type="number" min="1" value="1"></label>
      </div>
      <div class="row">
        <label class="grow">邀请码失效时间<input id="invite-expires" placeholder="2026-12-31T23:59:59Z"></label>
        <label class="grow">权益失效时间<input id="entitlement-expires" placeholder="2027-01-31T23:59:59Z"></label>
      </div>
      <button type="submit">新建邀请码</button>
    </form>
    <div id="created" class="ok hidden"></div>
    <h2>已有邀请码</h2>
    <div id="invite-table"></div>
  </section>
  <section id="users" class="hidden">
    <p class="muted">使用迁移角色。列表不含密码、会话和邀请码哈希。</p>
    <div id="user-table"></div>
    <div id="detail"></div>
  </section>
  <section id="logs" class="hidden">
    <div class="row">
      <label>分组
        <select id="log-group">
          <option value="all">全部</option>
          <option value="api">API</option>
          <option value="worker">Worker</option>
          <option value="qq">QQ</option>
          <option value="infra">基础设施</option>
          <option value="web-dev">前端开发</option>
          <option value="web-gateway">前端网关</option>
          <option value="hpagent-migrate">迁移任务</option>
          <option value="temporal-web">Temporal UI</option>
        </select>
      </label>
      <label>尾部行数
        <select id="log-tail">
          <option>100</option>
          <option selected>200</option>
          <option>500</option>
          <option>1000</option>
        </select>
      </label>
      <button type="button" id="log-refresh">读取</button>
    </div>
    <pre id="log-output">尚未读取。</pre>
  </section>
  <section id="sql" class="hidden">
    <p class="muted">应用库支持多条 SQL，按顺序展示各条结果；每组最多显示 500 行（并非数据库读取上限，大查询请自行加 LIMIT）。默认整批提交，任一错误则回滚；显式 COMMIT 会提前提交。Temporal 和 Hindsight 经 docker compose exec 进入对应 Postgres。</p>
    <div class="row">
      <label class="grow">目标<select id="sql-target"></select></label>
      <label class="grow">填入<select id="sql-preset"><option value="">不填入</option></select></label>
    </div>
    <textarea id="sql-text" spellcheck="false"></textarea>
    <button type="button" id="sql-run">执行</button>
    <div id="sql-result"></div>
  </section>
</main>
<script>
const consoleToken = "__HPAGENT_CONSOLE_TOKEN__";
const $ = (id) => document.getElementById(id);
function esc(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}
function show(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function setStatus(text, bad) {
  const node = $("status");
  node.textContent = text || "";
  node.className = bad ? "error" : "";
}
async function api(path, options) {
  const headers = new Headers(options?.headers);
  headers.set("X-HpAgent-Admin-Token", consoleToken);
  let response;
  try {
    response = await fetch(path, {...options, headers});
  } catch (error) {
    if (options?.method === "POST") {
      throw new Error("连接中断，操作结果未知；请先核对状态，再决定是否重试。");
    }
    throw error;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}
function table(rows, columns, onClick) {
  if (!rows || !rows.length) return '<p class="muted">无记录</p>';
  const cols = columns || Object.keys(rows[0]);
  const head = "<tr>" + cols.map((col) => `<th>${esc(col)}</th>`).join("") + "</tr>";
  const body = rows.map((row) => {
    const attr = onClick ? ` class="click" data-id="${esc(row.account_id)}"` : "";
    const cells = cols.map((col) => `<td>${esc(show(row[col]))}</td>`).join("");
    return `<tr${attr}>${cells}</tr>`;
  }).join("");
  return `<div class="wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}
function inviteStatus(row) {
  if (row.revoked_at) return "已撤销";
  if (row.expires_at && Date.parse(row.expires_at) <= Date.now()) return "已过期";
  if (Number(row.redemption_count) >= Number(row.max_redemptions)) return "已用尽";
  return "可兑换";
}
document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((item) => item.classList.remove("on"));
    button.classList.add("on");
    ["invites", "users", "logs", "sql"].forEach((id) => $(id).classList.toggle("hidden", id !== button.dataset.tab));
    if (button.dataset.tab === "invites") loadInvites();
    if (button.dataset.tab === "users") loadUsers();
    if (button.dataset.tab === "sql") loadSqlMeta().catch((error) => setStatus(error.message, true));
  });
});
document.querySelectorAll("[data-limit]").forEach((button) => {
  button.addEventListener("click", () => { $("daily").value = button.dataset.limit; });
});
$("invite-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.target.querySelector('button[type="submit"]');
  if (submit.disabled) return;
  const daily = $("daily").value.trim();
  const body = {
    daily_token_limit: daily ? Number(daily) : null,
    prompt_visibility: $("visibility").value,
    max_redemptions: Number($("redemptions").value || "1"),
    invite_expires_at: $("invite-expires").value.trim() || null,
    entitlement_expires_at: $("entitlement-expires").value.trim() || null
  };
  setStatus("正在创建邀请码…");
  submit.disabled = true;
  try {
    const created = await api("/api/invites", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    $("created").classList.remove("hidden");
    $("created").innerHTML = `<div>invite_id ${esc(created.invite_id)}</div><div>invite_code <b id="new-code">${esc(created.invite_code)}</b></div><button type="button" id="copy-code">复制邀请码</button><div class="muted">刷新后明文消失。</div>`;
    $("copy-code").addEventListener("click", async () => {
      await navigator.clipboard.writeText(created.invite_code);
      setStatus("已复制邀请码");
    });
    setStatus("邀请码已创建");
    await loadInvites();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    submit.disabled = false;
  }
});
async function loadInvites() {
  try {
    const payload = await api("/api/invites");
    const rows = payload.invites.map((row) => ({status: inviteStatus(row), ...row}));
    $("invite-table").innerHTML = table(rows, ["status", "invite_id", "entitlement_profile", "max_redemptions", "redemption_count", "expires_at", "revoked_at", "created_at"]);
  } catch (error) {
    setStatus(error.message, true);
  }
}
async function loadUsers() {
  setStatus("正在读取用户…");
  try {
    const payload = await api("/api/users");
    $("user-table").innerHTML = table(payload.users, null, true);
    $("user-table").querySelectorAll("tr.click").forEach((row) => {
      row.addEventListener("click", () => loadUser(row.dataset.id));
    });
    setStatus(`用户 ${payload.users.length}`);
  } catch (error) {
    setStatus(error.message, true);
  }
}
function block(title, rows, columns, truncated) {
  const note = truncated ? '<p class="muted">仅显示最近 200 条</p>' : "";
  return `<h2>${esc(title)}</h2>${note}${table(rows, columns)}`;
}
async function loadUser(accountId) {
  setStatus("正在读取用户明细…");
  try {
    const detail = await api("/api/users/" + encodeURIComponent(accountId));
    const parts = [];
    parts.push(block("账户", [detail.account]));
    parts.push(block("身份", detail.bindings));
    parts.push(block("权益", detail.entitlement ? [detail.entitlement] : []));
    parts.push(block("来源邀请", detail.invite ? [detail.invite] : []));
    parts.push("<h2>登录凭据</h2>" + (detail.credentials.length ? table(detail.credentials) : '<p class="muted">无登录密码</p>'));
    parts.push(block("会话", detail.sessions, null, detail.sessions_truncated));
    parts.push(block("近 7 日额度", detail.budgets));
    parts.push(block("QQ 绑定挑战", detail.challenges, null, detail.challenges_truncated));
    parts.push(block("工作区", detail.workspace ? [detail.workspace] : []));
    parts.push(block("规模", detail.counts ? [detail.counts] : []));
    parts.push(block("会话标题", detail.conversations, null, detail.conversations_truncated));
    parts.push(block("委托", detail.works, null, detail.works_truncated));
    $("detail").innerHTML = parts.join("");
    setStatus("用户明细已加载");
  } catch (error) {
    setStatus(error.message, true);
  }
}
$("log-refresh").addEventListener("click", async () => {
  const group = $("log-group").value;
  const tail = $("log-tail").value;
  setStatus("正在读取日志…");
  try {
    const payload = await api("/api/logs?group=" + encodeURIComponent(group) + "&tail=" + encodeURIComponent(tail));
    $("log-output").textContent = payload.text || "（空）";
    setStatus(payload.truncated ? "日志已截断" : "日志已读取");
  } catch (error) {
    setStatus(error.message, true);
  }
});
let presets = [];
let sqlReady = false;
async function loadSqlMeta() {
  if (sqlReady) return;
  const [targets, presetPayload] = await Promise.all([api("/api/sql-targets"), api("/api/sql-presets")]);
  $("sql-target").innerHTML = targets.targets.map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join("");
  presets = presetPayload.presets;
  $("sql-preset").insertAdjacentHTML("beforeend", presets.map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join(""));
  sqlReady = true;
}
$("sql-preset").addEventListener("change", () => {
  const preset = presets.find((item) => item.id === $("sql-preset").value);
  if (preset) $("sql-text").value = preset.sql;
});
function renderSqlResult(payload) {
  if (payload.kind === "batch") {
    return payload.results.map((result, index) =>
      `<h2>结果 ${index + 1}</h2>${renderSqlResult(result)}`
    ).join("");
  }
  if (payload.kind === "rows") {
    const status = `<p>${esc(payload.status || "")} · rowcount ${esc(payload.rowcount)}</p>`;
    const note = payload.truncated ? '<p class="muted">仅显示前 500 行</p>' : "";
    return status + note + table(payload.rows, payload.columns);
  }
  if (payload.kind === "command") {
    return `<p>${esc(payload.status || "")} · rowcount ${esc(payload.rowcount)}</p>`;
  }
  const note = payload.truncated ? "输出已截断\n" : "";
  return `<pre>${esc(note + (payload.text || ""))}</pre>`;
}
$("sql-run").addEventListener("click", async () => {
  setStatus("正在执行 SQL…");
  $("sql-run").disabled = true;
  try {
    const payload = await api("/api/sql", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({target: $("sql-target").value, sql: $("sql-text").value})
    });
    $("sql-result").innerHTML = renderSqlResult(payload);
    setStatus("SQL 已执行");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("sql-run").disabled = false;
  }
});
loadInvites();
</script>
</body>
</html>
"""


def make_handler() -> type[BaseHTTPRequestHandler]:
    # A trusted loopback navigation bootstraps the token into this page only.
    # Other origins cannot read it; every API request must send it as a header.
    request_token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)

            def respond() -> None:
                if parsed.path == "/":
                    self._send(
                        HTTPStatus.OK, HTML.replace("__HPAGENT_CONSOLE_TOKEN__", request_token),
                        "text/html; charset=utf-8",
                    )
                    return
                if parsed.path == "/api/invites":
                    self._json(HTTPStatus.OK, list_invites())
                    return
                if parsed.path == "/api/users":
                    self._json(HTTPStatus.OK, list_users())
                    return
                if parsed.path.startswith("/api/users/"):
                    account_id = unquote(parsed.path[len("/api/users/"):]).strip("/")
                    self._json(HTTPStatus.OK, user_detail(account_id))
                    return
                if parsed.path == "/api/logs":
                    params = parse_qs(parsed.query)
                    group = (params.get("group") or ["all"])[0]
                    tail = query_int(params, "tail", 200)
                    self._json(HTTPStatus.OK, fetch_logs(group, tail))
                    return
                if parsed.path == "/api/sql-targets":
                    self._json(HTTPStatus.OK, public_targets())
                    return
                if parsed.path == "/api/sql-presets":
                    self._json(HTTPStatus.OK, public_presets())
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            self._guard(respond)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)

            def respond() -> None:
                body = read_body(self)
                if parsed.path == "/api/invites":
                    self._json(HTTPStatus.OK, create_invite(body))
                    return
                if parsed.path == "/api/sql":
                    self._json(HTTPStatus.OK, execute_sql(body.get("target"), body.get("sql")))
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            self._guard(respond)

        def _guard(self, respond: Any) -> None:
            try:
                self._check_request()
                respond()
            except ConsoleError as exc:
                self._json(exc.status, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                message = redact(str(exc)) or exc.__class__.__name__
                print(f"admin console error: {message}", file=sys.stderr)
                try:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": message})
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _check_request(self) -> None:
            port = self.server.server_address[1]
            authorities = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if port == 80:
                authorities.update({"127.0.0.1", "localhost"})
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0] not in authorities:
                raise ConsoleError("invalid Host", HTTPStatus.FORBIDDEN)
            origins = self.headers.get_all("Origin", [])
            if origins and origins != [f"http://{hosts[0]}"]:
                raise ConsoleError("cross-origin request denied", HTTPStatus.FORBIDDEN)
            if self.headers.get("Sec-Fetch-Site", "none") not in {"none", "same-origin"}:
                raise ConsoleError("cross-site request denied", HTTPStatus.FORBIDDEN)
            if urlparse(self.path).path.startswith("/api/"):
                tokens = self.headers.get_all("X-HpAgent-Admin-Token", [])
                if len(tokens) != 1 or not secrets.compare_digest(
                    tokens[0].encode("utf-8"), request_token.encode("ascii"),
                ):
                    raise ConsoleError("invalid console token", HTTPStatus.FORBIDDEN)

        def _json(self, status: HTTPStatus, body: Any) -> None:
            self._send(status, dumps(body), "application/json; charset=utf-8")

        def _send(self, status: HTTPStatus, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local admin page for invites, users, Docker logs, and SQL"
    )
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    server = ThreadingHTTPServer((args.host, args.port), make_handler())
    print(f"HpAgent admin console: http://{args.host}:{args.port}")
    print(f"  app database: {APP_HOST}:{APP_PORT}/{APP_DATABASE}")
    print("  temporal: docker compose exec temporal-postgres")
    print("  hindsight: docker compose exec hindsight-postgres")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
