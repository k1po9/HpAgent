from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest


def _load():
    path = Path(__file__).parents[1] / "scripts" / "operations" / "admin-console.py"
    spec = importlib.util.spec_from_file_location("admin_console", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


console = _load()


@pytest.fixture
def http_console(monkeypatch: pytest.MonkeyPatch):
    sql = MagicMock(return_value={"kind": "command", "status": "UPDATE 1"})
    users = MagicMock(return_value={"users": []})
    invites = MagicMock(return_value={"invite_id": "new", "invite_code": "one-time"})
    monkeypatch.setattr(console, "execute_sql", sql)
    monkeypatch.setattr(console, "list_users", users)
    monkeypatch.setattr(console, "create_invite", invites)
    server = ThreadingHTTPServer(("127.0.0.1", 0), console.make_handler())
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()

    def request(path="/", *, method="GET", headers=None, body=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    try:
        status, _, html = request()
        assert status == 200
        token = re.search(r'const consoleToken = "([A-Za-z0-9_-]+)";', html).group(1)
        yield SimpleNamespace(
            request=request, token=token, port=server.server_port,
            sql=sql, users=users, invites=invites,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize(("path", "method"), [
    ("/api/users", "GET"), ("/api/sql", "POST"), ("/api/invites", "POST"),
])
@pytest.mark.parametrize("token", [None, "wrong-token", "\xe9"])
def test_api_rejects_missing_or_wrong_token_before_access(http_console, path, method, token):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-HpAgent-Admin-Token"] = token
    status, _, _ = http_console.request(path, method=method, headers=headers, body="{}")
    assert status == 403
    http_console.sql.assert_not_called()
    http_console.users.assert_not_called()
    http_console.invites.assert_not_called()


@pytest.mark.parametrize("extra_headers", [
    {"Origin": "https://untrusted.example"}, {"Origin": "null"},
    {"Host": "untrusted.example"}, {"Host": "127.0.0.1:1"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
])
@pytest.mark.parametrize("path", ["/", "/api/sql"])
def test_console_rejects_cross_origin_and_rebinding_before_access(http_console, extra_headers, path):
    headers = {
        "X-HpAgent-Admin-Token": http_console.token,
        "Content-Type": "application/json", **extra_headers,
    }
    status, _, _ = http_console.request(
        path, method="GET" if path == "/" else "POST", headers=headers, body="{}",
    )
    assert status == 403
    http_console.sql.assert_not_called()


def test_trusted_page_and_api_work_without_exposing_token_in_url(http_console):
    status, headers, html = http_console.request()
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "__HPAGENT_CONSOLE_TOKEN__" not in html
    headers = {
        "X-HpAgent-Admin-Token": http_console.token,
        "Content-Type": "application/json",
        "Origin": f"http://127.0.0.1:{http_console.port}",
        "Sec-Fetch-Site": "same-origin",
    }
    status, _, _ = http_console.request("/api/users", headers=headers)
    assert status == 200
    http_console.users.assert_called_once_with()
    status, _, body = http_console.request(
        "/api/sql", method="POST", headers=headers,
        body=json.dumps({"target": "app-migrate", "sql": "UPDATE t SET n=1"}),
    )
    assert status == 200
    assert json.loads(body)["status"] == "UPDATE 1"
    http_console.sql.assert_called_once_with("app-migrate", "UPDATE t SET n=1")
    status, _, _ = http_console.request(
        "/api/invites", method="POST", headers=headers, body='{"daily_token_limit":5000}',
    )
    assert status == 200
    http_console.invites.assert_called_once_with({"daily_token_limit": 5000})


@pytest.mark.parametrize(("headers", "body", "expected"), [
    ({"Content-Type": "text/plain"}, "{}", 415),
    ({"Content-Type": "application/json", "Content-Length": "bad"}, "{}", 400),
    ({"Content-Type": "application/json", "Content-Length": "-1"}, "{}", 400),
    ({"Content-Type": "application/json", "Content-Length": "1000001"}, "{}", 400),
    ({"Content-Type": "application/json", "Transfer-Encoding": "chunked"}, "{}", 400),
    ({"Content-Type": "application/json"}, "not JSON", 400),
    ({"Content-Type": "application/json"}, "[]", 400),
])
def test_sql_rejects_invalid_request_body_before_execute(http_console, headers, body, expected):
    status, _, _ = http_console.request(
        "/api/sql", method="POST",
        headers={"X-HpAgent-Admin-Token": http_console.token, **headers}, body=body,
    )
    assert status == expected
    http_console.sql.assert_not_called()


def test_non_loopback_bind_is_rejected_before_loading_secrets(monkeypatch):
    load = MagicMock()
    monkeypatch.setattr(console, "load_dotenv", load)
    monkeypatch.setattr(sys, "argv", ["admin-console.py", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit) as exc:
        console.main()
    assert exc.value.code == 2
    load.assert_not_called()


def test_fixed_queries_omit_secret_columns() -> None:
    blob = "\n".join(console.FIXED_SQL_STATEMENTS)
    for name in (
        "password_hash",
        "token_hash",
        "csrf_secret_hash",
        "code_hash",
        "challenge_code_hash",
    ):
        assert name not in blob


def test_log_groups_match_the_operations_script() -> None:
    assert console.log_argv("all", 200) == [
        *console.COMPOSE_PREFIX, "logs", "--tail", "200",
    ]
    assert console.log_argv("api", 100)[-1] == "hpagent-api"
    assert console.log_argv("worker", 500)[-2:] == ["hpagent", "hpagent-document-worker"]
    assert console.log_argv("qq", 100)[-2:] == ["hpagent", "snowluma"]
    assert console.log_argv("infra", 1000)[-8:] == [
        "app-postgres", "redis", "temporal", "temporal-postgres",
        "hindsight", "hindsight-postgres", "searxng", "gotenberg",
    ]
    with pytest.raises(console.ConsoleError):
        console.log_argv("api", 50)
    with pytest.raises(console.ConsoleError):
        console.log_argv("shell", 100)


def test_psql_argv_keeps_the_password_on_stdin() -> None:
    argv = console.psql_argv("temporal-postgres", "temporal", "temporal")
    joined = " ".join(argv)
    assert "PGPASSWORD=" not in joined
    assert "fixture-password" not in joined
    assert "127.0.0.1" in joined
    assert "temporal-postgres" in argv
    payload = console.psql_stdin("fixture-password", "SELECT 1")
    assert payload.startswith(b"fixture-password\n")
    assert b"SELECT 1" in payload
    with pytest.raises(console.ConsoleError):
        console.psql_stdin("line\nbreak", "SELECT 1")


def test_invite_request_forces_standard_tier() -> None:
    profile, max_redemptions, invite_expires = console.invite_request({
        "daily_token_limit": 50000,
        "model_access_tier": "owner",
        "prompt_visibility": "none",
        "max_redemptions": 5,
    })
    assert profile.model_access_tier == "standard"
    assert profile.daily_token_limit == 50000
    assert profile.prompt_visibility == "none"
    assert max_redemptions == 5
    assert invite_expires is None


def test_invite_request_rejects_naive_timestamp_and_bad_limit() -> None:
    with pytest.raises(console.ConsoleError):
        console.invite_request({"invite_expires_at": "2026-12-31T23:59:59"})
    with pytest.raises(console.ConsoleError):
        console.invite_request({"daily_token_limit": 0})
    with pytest.raises(console.ConsoleError):
        console.invite_request({"prompt_visibility": "owner"})


def test_create_invite_uses_registration_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HPAGENT_MIGRATE_PASSWORD", "fixture-password")
    captured: dict[str, object] = {}

    class FakeService:
        def __init__(self, dsn: str) -> None:
            captured["dsn"] = dsn

        def create(self, profile: object, *, max_redemptions: int, expires_at: object) -> object:
            captured["profile"] = profile
            captured["max_redemptions"] = max_redemptions
            captured["expires_at"] = expires_at
            return SimpleNamespace(invite_id="invite-1", code="plain-code")

    monkeypatch.setattr("account.invite_service.RegistrationInviteService", FakeService)
    created = console.create_invite({"daily_token_limit": 5000})
    assert created == {"invite_id": "invite-1", "invite_code": "plain-code"}
    dsn = str(captured["dsn"])
    assert "hpagent_migrate" in dsn
    assert "5434" in dsn
    assert "fixture-password" in dsn
    profile = captured["profile"]
    assert profile.model_access_tier == "standard"
    assert profile.daily_token_limit == 5000
    assert captured["max_redemptions"] == 1
    assert captured["expires_at"] is None


def test_redact_removes_configured_passwords(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HPAGENT_MIGRATE_PASSWORD", "migrate-secret")
    assert console.redact("failed migrate-secret") == "failed ***"


def test_json_default_serializes_uuid_and_bytes() -> None:
    value = UUID("00000000-0000-0000-0000-000000000001")
    assert console.json_default(value) == str(value)
    assert console.json_default(b"\x00\xff") == "00ff"


def test_hindsight_database_name_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    target = console.SQL_TARGET_BY_KEY["hindsight"]
    monkeypatch.delenv("HINDSIGHT_DB_NAME", raising=False)
    assert console.target_database(target) == "hindsight"
    monkeypatch.setenv("HINDSIGHT_DB_NAME", "memory_next")
    assert console.target_database(target) == "memory_next"


def test_page_exposes_the_four_panels() -> None:
    html = console.HTML
    for label in ("邀请码", "用户", "Docker 日志", "SQL"):
        assert label in html
    assert "/api/invites" in html
    assert "/api/users/" in html
    assert "/api/logs?group=" in html
    assert "/api/sql" in html
    assert "data-limit=\"5000\"" in html
    assert "data-limit=\"50000\"" in html
    assert "data-limit=\"100000\"" in html


def test_page_javascript_is_valid() -> None:
    script = console.HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    result = subprocess.run(["node", "--check"], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_statement_returns_every_result_with_independent_limits() -> None:
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.nextset.side_effect = [True, True, True, None]
    descriptions = [None, [SimpleNamespace(name="account_id")],
                    [SimpleNamespace(name="n")], None]
    type(cursor).description = property(lambda _: descriptions[cursor.nextset.call_count])
    statuses = ["UPDATE 2", "SELECT 502", "SELECT 1", "UPDATE 0"]
    type(cursor).statusmessage = property(lambda _: statuses[cursor.nextset.call_count])
    counts = [2, 502, 1, 0]
    type(cursor).rowcount = property(lambda _: counts[cursor.nextset.call_count])
    cursor.fetchmany.side_effect = [
        [{"account_id": i} for i in range(console.ROW_LIMIT + 1)],
        [{"n": 1}],
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    sql = "UPDATE accounts SET status='active'; SELECT account_id FROM accounts; SELECT 1; UPDATE accounts SET status='active' WHERE false"
    result = console.execute_statement(conn, sql)
    conn.cursor.assert_called_once_with()
    cursor.execute.assert_called_once_with(sql)
    assert result["kind"] == "batch"
    first, large, small, last = result["results"]
    assert first == {"kind": "command", "rowcount": 2, "status": "UPDATE 2"}
    assert large["columns"] == ["account_id"]
    assert len(large["rows"]) == console.ROW_LIMIT
    assert large["truncated"] is True
    assert large["rowcount"] == 502
    assert small["rows"] == [{"n": 1}]
    assert small["truncated"] is False
    assert last == {"kind": "command", "rowcount": 0, "status": "UPDATE 0"}


@pytest.mark.parametrize("has_rows", [False, True])
def test_single_statement_keeps_response_shape(has_rows: bool) -> None:
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.description = [SimpleNamespace(name="id")] if has_rows else None
    cursor.fetchmany.return_value = [{"id": 1}]
    cursor.rowcount = 1
    cursor.statusmessage = "UPDATE 1"
    cursor.nextset.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    result = console.execute_statement(conn, "UPDATE t SET id=1 RETURNING id")
    assert result["kind"] == ("rows" if has_rows else "command")
    assert "results" not in result


def test_tcp_applies_session_settings_before_execute_and_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    monkeypatch.setattr(console, "connect_app", lambda *_: conn)
    expected = {"kind": "command", "rowcount": 1, "status": "UPDATE 1"}

    def execute(connection: object, sql: str) -> dict:
        assert connection is conn
        assert sql.startswith("WITH")
        assert [call.args[0] for call in conn.execute.call_args_list] == [
            "SET search_path TO hpagent, public",
            "SET statement_timeout = '30s'",
            "SET lock_timeout = '5s'",
        ]
        conn.rollback.assert_not_called()
        conn.commit.assert_not_called()
        return expected

    monkeypatch.setattr(console, "execute_statement", execute)
    result = console.execute_tcp(console.SQL_TARGET_BY_KEY["app-migrate"], "WITH x AS (...) UPDATE t SET n=1")
    assert result == expected
    conn.commit.assert_called_once_with()
    conn.rollback.assert_not_called()
    conn.close.assert_called_once_with()


@pytest.mark.parametrize("fail_on_nextset", [False, True])
def test_tcp_errors_roll_back_without_retry(monkeypatch: pytest.MonkeyPatch, fail_on_nextset: bool) -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    error = console.psycopg.errors.FeatureNotSupported("unsupported SQL")
    if fail_on_nextset:
        cursor.description = None
        cursor.nextset.side_effect = error
    else:
        cursor.execute.side_effect = error
    monkeypatch.setattr(console, "connect_app", lambda *_: conn)
    sql = "WITH x AS (SELECT 1) SELECT * FROM x; SELECT 2"
    with pytest.raises(console.ConsoleError, match="unsupported SQL"):
        console.execute_tcp(console.SQL_TARGET_BY_KEY["app-migrate"], sql)
    cursor.execute.assert_called_once_with(sql)
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once_with()
    conn.close.assert_called_once_with()
