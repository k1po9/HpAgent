from pathlib import Path

from fastapi.routing import APIRoute

from web_api.app import create_app
from web_api.config import WebApiSettings


def test_migration_defines_scoped_single_use_approval_and_commands():
    sql = Path("persistence/migrations/029_file_action_approvals.sql").read_text()
    assert "UNIQUE(run_id, operation_id)" in sql
    assert "FOREIGN KEY(account_id,conversation_id,run_id)" in sql
    assert "arguments_hash char(64) NOT NULL" in sql
    assert "'pending','approved','rejected','expired','consumed'" in sql
    assert "approve_file_action" in sql
    assert "reject_file_action" in sql


def test_migration_031_adds_persistent_revisions_and_recoverable_binding():
    sql = Path("persistence/migrations/031_persistent_web_file_foundation.sql").read_text()
    assert "CREATE TABLE persistent_file_destinations" in sql
    assert "CREATE TABLE persistent_file_revisions" in sql
    assert "UNIQUE(account_id,logical_path)" in sql
    assert "UNIQUE(destination_id,operation_id)" in sql
    assert "execution_id=operation_id" in sql
    assert "execution_fencing_token >= 1" in sql
    assert "FOREIGN KEY(account_id,current_file_id)" in sql


def test_api_exposes_owned_list_and_idempotent_decision_routes():
    app = create_app(WebApiSettings(
        database_url="postgresql://unused",
        public_origin="https://localhost",
        cursor_signing_keys={"v1": b"test"},
        active_cursor_key_id="v1",
        session_token_pepper=b"test",
        csrf_signing_key=b"test",
    ))
    routes = {
        (route.path, tuple(sorted(route.methods or set())))
        for route in app.routes if isinstance(route, APIRoute)
    }
    assert ("/api/v1/runs/{run_id}/file-action-approvals", ("GET",)) in routes
    assert ("/api/v1/file-action-approvals/{approval_id}/approve", ("POST",)) in routes
    assert ("/api/v1/file-action-approvals/{approval_id}/reject", ("POST",)) in routes
