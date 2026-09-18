from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from conversation_domain.commands import CommandService
from resources.model_client import ModelClient
from resources.model_input_snapshot import SnapshotRepository

pytestmark = pytest.mark.postgres

PROMPT_SENTINEL = "W5-D-PROMPT-MUST-NOT-LEAK"


def login(client, username="alice"):
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "correct-password", "return_to": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _conversation_and_run(database_url, account_id):
    service = CommandService(database_url)
    conversation_id = UUID(
        service.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    run_id = UUID(service.send_message(
        account_id, conversation_id, str(uuid4()), "hello"
    )["run_id"])
    return service, conversation_id, run_id


def _snapshot(db, database_url, worker_database_url, account_id, visibility="summary"):
    db.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) "
        "VALUES (%s,'standard',%s)",
        (account_id, visibility),
    )
    _service, conversation_id, run_id = _conversation_and_run(database_url, account_id)
    prepared = ModelClient({
        "api_key": "CREDENTIAL-MUST-NOT-APPEAR",
        "base_url": "https://example.test/v1",
        "model": "model-a",
        "endpoint_id": "example:0",
        "provider": "example",
        "api_format": "openai",
    }).prepare_request(
        [{"role": "user", "content": PROMPT_SENTINEL}],
        tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
    )
    model_call_id = uuid4()
    snapshot = SnapshotRepository(worker_database_url).create(
        account_id=account_id,
        run_id=run_id,
        model_call_id=model_call_id,
        operation_id=f"model:{model_call_id}:a1:f1:test",
        execution_attempt=1,
        call_ordinal=1,
        fallback_attempt=1,
        phase="decision",
        entitlement_version=1,
        prepared=prepared,
    )
    return conversation_id, run_id, snapshot, prepared.body()


def test_owned_list_and_visibility_projections_are_current_and_immutable(
    db, seed_identity, client_factory, database_url, worker_database_url,
):
    account_id = seed_identity("alice")
    _conversation_id, run_id, snapshot, body = _snapshot(
        db, database_url, worker_database_url, account_id
    )
    client = client_factory()
    login(client)

    listed = client.get(f"/api/v1/runs/{run_id}/model-inputs")
    assert listed.status_code == 200
    summary = listed.json()
    assert summary["visibility"] == "summary"
    assert len(summary["items"]) == 1
    item = summary["items"][0]
    assert set(item) == {
        "snapshot_id", "content_hash", "model_call_id", "phase", "fallback_attempt",
        "endpoint_id", "provider", "model", "api_format", "created_at", "message_count",
        "tool_count", "resolved_url", "dispatch_status",
    }
    assert item["resolved_url"] == "https://example.test/v1/chat/completions"
    assert item["dispatch_status"] == "not_dispatched"
    assert PROMPT_SENTINEL not in json.dumps(summary)
    assert item["message_count"] == 1
    assert item["tool_count"] == 1

    summary_detail = client.get(f"/api/v1/model-inputs/{snapshot.snapshot_id}").json()
    assert summary_detail["visibility"] == "summary"
    assert "provider_request_body" not in summary_detail["model_input"]

    db.execute(
        "UPDATE account_entitlements SET prompt_visibility='full_safe',version=version+1 "
        "WHERE account_id=%s", (account_id,),
    )
    full = client.get(f"/api/v1/model-inputs/{snapshot.snapshot_id}").json()
    assert full["visibility"] == "full_safe"
    assert full["model_input"]["provider_request_body"] == body
    assert full["model_input"]["snapshot_id"] == item["snapshot_id"]
    assert full["model_input"]["content_hash"] == item["content_hash"]

    stored = db.execute(
        "SELECT provider_request_body FROM model_input_snapshots WHERE snapshot_id=%s",
        (snapshot.snapshot_id,),
    ).fetchone()[0]
    assert stored == body

    db.execute(
        "UPDATE account_entitlements SET prompt_visibility='none',version=version+1 "
        "WHERE account_id=%s", (account_id,),
    )
    none_list = client.get(f"/api/v1/runs/{run_id}/model-inputs").json()
    assert none_list == {
        "visibility": "none",
        "items": [{
            "snapshot_id": item["snapshot_id"],
            "content_hash": item["content_hash"],
            "model_call_id": item["model_call_id"],
            "dispatch_status": "not_dispatched",
        }],
    }
    denied = client.get(f"/api/v1/model-inputs/{snapshot.snapshot_id}")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "model_input_unavailable"
    assert PROMPT_SENTINEL not in denied.text


def test_foreign_and_missing_snapshots_share_not_found_behavior(
    db, seed_identity, client_factory, database_url, worker_database_url,
):
    owner_id = seed_identity("owner")
    _conversation_id, run_id, snapshot, _body = _snapshot(
        db, database_url, worker_database_url, owner_id, "full_safe"
    )
    foreign_id = seed_identity("foreign")
    db.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) "
        "VALUES (%s,'standard','full_safe')", (foreign_id,),
    )
    client = client_factory()
    login(client, "foreign")

    for path in (
        f"/api/v1/runs/{run_id}/model-inputs",
        f"/api/v1/model-inputs/{snapshot.snapshot_id}",
        f"/api/v1/model-inputs/{uuid4()}",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "resource_not_found"


@pytest.mark.parametrize("entitlement_state", ["expired", "disabled"])
def test_unavailable_entitlement_has_none_semantics(
    db, seed_identity, client_factory, database_url, worker_database_url, entitlement_state,
):
    account_id = seed_identity("alice")
    _conversation_id, run_id, snapshot, _body = _snapshot(
        db, database_url, worker_database_url, account_id, "full_safe"
    )
    if entitlement_state == "expired":
        db.execute(
            "UPDATE account_entitlements SET expires_at=%s WHERE account_id=%s",
            (datetime.now(UTC) - timedelta(seconds=1), account_id),
        )
    else:
        db.execute("UPDATE accounts SET status='disabled' WHERE account_id=%s", (account_id,))
    client = client_factory()
    if entitlement_state == "disabled":
        # The authenticated session is established before Account liveness is revoked.
        db.execute("UPDATE accounts SET status='active' WHERE account_id=%s", (account_id,))
        login(client)
        db.execute("UPDATE accounts SET status='disabled' WHERE account_id=%s", (account_id,))
        assert client.get(f"/api/v1/model-inputs/{snapshot.snapshot_id}").status_code == 401
        return
    login(client)
    assert client.get(f"/api/v1/runs/{run_id}/model-inputs").json()["visibility"] == "none"
    assert client.get(f"/api/v1/model-inputs/{snapshot.snapshot_id}").status_code == 403


def test_trace_api_contains_refs_but_not_canonical_prompt(
    db, seed_identity, client_factory, database_url, worker_database_url,
):
    account_id = seed_identity("alice")
    conversation_id, run_id, snapshot, _body = _snapshot(
        db, database_url, worker_database_url, account_id, "full_safe"
    )
    trace_run_id, trace_event_id = uuid4(), uuid4()
    db.execute(
        "INSERT INTO trace_runs(trace_run_id,run_id,account_id,conversation_id,strategy) "
        "VALUES (%s,%s,%s,%s,'react')",
        (trace_run_id, run_id, account_id, conversation_id),
    )
    db.execute(
        "INSERT INTO trace_events(trace_event_id,trace_run_id,event_type,name,metadata) "
        "VALUES (%s,%s,'llm','LLMCall',%s::jsonb)",
        (trace_event_id, trace_run_id, json.dumps({
            "snapshot_id": str(snapshot.snapshot_id),
            "content_hash": snapshot.content_hash.hex(),
            "model_call_id": str(snapshot.model_call_id),
            "fallback_attempt": 1,
        })),
    )
    client = client_factory()
    login(client)
    trace = client.get(f"/api/v1/runs/{run_id}/trace")
    assert trace.status_code == 200
    assert str(snapshot.snapshot_id) in trace.text
    assert PROMPT_SENTINEL not in trace.text
    assert "provider_request_body" not in trace.text
