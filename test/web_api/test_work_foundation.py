"""Phase 1 Work commands through the authenticated HTTP boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_generic_completion_contract import CONTRACT

pytestmark = pytest.mark.postgres


def test_past_once_returns_structured_error_without_creating_work(seed_identity, client_factory, db):
    account = seed_identity('past-time-owner')
    client = client_factory()
    csrf = login(client, 'past-time-owner')
    req = {**requirement('Meeting'), 'timing': {
        'schema_version': 1, 'kind': 'once', 'timezone': 'Asia/Shanghai',
        'due_at': (datetime.now(UTC) - timedelta(days=365)).isoformat(),
    }}
    response = client.post('/api/v1/works', json={'title': 'Meeting', 'requirement': req},
                           headers=headers(csrf, str(uuid4())))
    assert response.status_code == 422, response.text
    error = response.json()['error']
    assert error['code'] == 'work_time_in_past' and error['retryable'] is False
    assert error['details']['due_at'] == req['timing']['due_at']
    assert datetime.fromisoformat(error['details']['server_now']) > datetime.fromisoformat(req['timing']['due_at'])
    assert db.execute('SELECT count(*) FROM works WHERE account_id=%s', (account,)).fetchone()[0] == 0


def test_generic_create_rejects_retired_criteria_without_claiming_key(seed_identity, client_factory, db):
    account = seed_identity('generic-contract-owner')
    client = client_factory()
    csrf = login(client, 'generic-contract-owner')
    key = str(uuid4())
    legacy = {**CONTRACT['new_requirement'], 'acceptance_criteria': CONTRACT['legacy_criteria']}
    rejected = client.post('/api/v1/works', json={'title': 'Legacy', 'requirement': legacy},
                           headers=headers(csrf, key))
    assert rejected.status_code == 422, rejected.text
    error = rejected.json()['error']
    assert error['code'] == 'generic_acceptance_revision_required'
    assert error['retryable'] is False
    assert error['details']['supported_result_types'] == CONTRACT['result_evidence_types']
    assert db.execute('SELECT count(*) FROM works WHERE account_id=%s', (account,)).fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM idempotency_commands WHERE account_id=%s AND idempotency_key=%s',
                      (account, key)).fetchone()[0] == 0
    accepted = client.post('/api/v1/works', json={'title': 'Typed', 'requirement': CONTRACT['new_requirement']},
                           headers=headers(csrf, key))
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()['work']['requirement']['acceptance_criteria'] == CONTRACT['new_requirement']['acceptance_criteria']


def test_legacy_generic_http_replay_read_and_explicit_revision_preserve_state(seed_identity, client_factory, db):
    seed_identity('legacy-contract-owner')
    client = client_factory()
    csrf = login(client, 'legacy-contract-owner')
    key = str(uuid4())
    legacy = {**CONTRACT['new_requirement'], 'acceptance_criteria': CONTRACT['legacy_criteria']}
    payload = {'title': 'Legacy', 'requirement': legacy}
    with patch('work_domain.commands.validate_new_completion_criteria'):
        accepted = client.post('/api/v1/works', json=payload, headers=headers(csrf, key))
    assert accepted.status_code == 201, accepted.text
    replay = client.post('/api/v1/works', json=payload, headers=headers(csrf, key))
    assert replay.status_code == 201 and replay.headers['Idempotency-Replayed'] == 'true'
    assert replay.json() == accepted.json()
    work_id = accepted.json()['work']['work_id']
    url = f'/api/v1/works/{work_id}'
    assert client.get(url).json()['work']['requirement']['acceptance_criteria'] == CONTRACT['legacy_criteria']
    paused = client.post(f'{url}/pause', json={}, headers=headers(csrf, str(uuid4()), accepted.headers['ETag']))
    before = paused.json()['work']
    revision_key = str(uuid4())
    rejected = client.post(f'{url}/revisions', json={'requirement': legacy, 'change_reason': 'No conversion'},
                           headers=headers(csrf, revision_key, paused.headers['ETag']))
    assert rejected.status_code == 422, rejected.text
    assert client.get(url).json()['work'] == before
    upgraded = {**legacy, 'acceptance_criteria': CONTRACT['upgraded_criteria']}
    revised = client.post(f'{url}/revisions', json={'requirement': upgraded, 'change_reason': 'Explicit output upgrade'},
                          headers=headers(csrf, revision_key, paused.headers['ETag']))
    assert revised.status_code == 200, revised.text
    after = revised.json()['work']
    assert after['status'] == before['status'] == 'paused'
    assert after['budget'] == before['budget'] and after['completion_receipt'] is None
    assert after['current_requirement_revision'] == 2
    assert db.execute('SELECT acceptance_criteria FROM work_requirements WHERE work_id=%s AND revision=1',
                      (work_id,)).fetchone()[0] == CONTRACT['legacy_criteria']


def headers(csrf: str, key: str, etag: str | None = None) -> dict[str, str]:
    value = {"Origin": "https://testserver", "X-CSRF-Token": csrf,
             "Idempotency-Key": key}
    if etag:
        value["If-Match"] = etag
    return value


def login(client, name: str) -> str:
    response = client.post("/auth/login", json={"username": name,
        "password": "correct-password"}, follow_redirects=False)
    assert response.status_code == 303
    return client.get("/api/v1/me").json()["csrf_token"]


def requirement(objective: str) -> dict:
    return {"objective": objective, "capability_key": "reminder",
            "spec": {"schema_version": 1, "content": "Remember this"},
            "acceptance_criteria": [{"id": "sent", "required": True,
                "evidence_types": ["operation_receipt"]}]}


def test_capability_query_is_owned_and_available_while_chat_runs(seed_identity, client_factory, db):
    account = seed_identity('s5-owner')
    seed_identity('s5-other')
    client = client_factory()
    csrf = login(client, 's5-owner')
    from conversation_domain.commands import CommandService
    commands = CommandService(client.app.state.api_pool)
    cid = commands.create_conversation(account, str(uuid4())).body['conversation_id']
    from uuid import UUID
    sent = commands.send_message(account, UUID(cid), str(uuid4()), 'Hello')
    run_id = sent['run_id']
    response = client.get(f'/api/v1/runs/{run_id}/capability-results')
    assert response.status_code == 200 and response.json() == {'items': [], 'truncated': False}
    source = db.execute('SELECT trigger_message_id FROM runs WHERE run_id=%s', (run_id,)).fetchone()[0]
    work = client.post('/api/v1/works', json={'title': 'Source', 'requirement': requirement('Goal'),
        'source_message_id': str(source)}, headers=headers(csrf, str(uuid4())))
    assert work.status_code == 201, work.text
    assert work.json()['work']['sources'] == [{'conversation_id': cid, 'source_message_id': str(source)}]
    login(client, 's5-other')
    assert client.get(f'/api/v1/runs/{run_id}/capability-results').status_code == 404
    assert client.get('/api/v1/works').json()['items'] == []


def test_work_http_lifecycle_and_cross_account_visibility(seed_identity, client_factory, db):
    account_id = seed_identity("work-owner")
    seed_identity("work-other")
    client = client_factory()
    csrf = login(client, "work-owner")
    key = str(uuid4())
    payload = {"title": "Reminder", "requirement": requirement("First objective")}
    accepted = client.post("/api/v1/works", json=payload, headers=headers(csrf, key))
    assert accepted.status_code == 201, accepted.text
    replay = client.post("/api/v1/works", json=payload, headers=headers(csrf, key))
    assert replay.status_code == 201 and replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == accepted.json()
    conflict = client.post("/api/v1/works", json={**payload, "title": "Changed"},
                           headers=headers(csrf, key))
    assert conflict.status_code == 409

    work_id = accepted.json()["work"]["work_id"]
    url = f"/api/v1/works/{work_id}"
    assert client.get(url).headers["ETag"] == accepted.headers["ETag"]
    conversations = [uuid4(), uuid4()]
    for conversation_id in conversations:
        db.execute("INSERT INTO conversations(account_id,conversation_id) VALUES (%s,%s)",
                   (account_id, conversation_id))
        linked = client.put(f"{url}/conversations/{conversation_id}", json={},
            headers=headers(csrf, str(uuid4()), client.get(url).headers["ETag"]))
        assert linked.status_code == 200, linked.text
    assert set(client.get(url).json()["work"]["conversation_ids"]) == set(map(str, conversations))
    assert any(item["work_id"] == work_id for item in client.get("/api/v1/works").json()["items"])

    current_etag = client.get(url).headers["ETag"]
    revised = client.post(f"{url}/revisions", json={"requirement": requirement("New objective")},
        headers=headers(csrf, str(uuid4()), current_etag))
    assert revised.status_code == 200, revised.text
    assert revised.json()["work"]["current_requirement_revision"] == 2
    stale = client.post(f"{url}/pause", json={}, headers=headers(csrf, str(uuid4()), current_etag))
    assert stale.status_code == 409 and stale.json()["error"]["details"]["current"]["row_version"] > 1
    # UI-6: acceptance uses the same version gate and cannot slip through a
    # stale UI snapshot, even before resolving a candidate Artifact version.
    stale_accept = client.post(f"{url}/accept-result",
        json={"requirement_revision": 1, "artifact_version_id": str(uuid4())},
        headers=headers(csrf, str(uuid4()), current_etag))
    assert stale_accept.status_code == 409, stale_accept.text
    assert stale_accept.json()["error"]["details"]["current"]["work_id"] == work_id
    assert db.execute("SELECT count(*) FROM work_result_acceptances WHERE work_id=%s", (work_id,)).fetchone()[0] == 0
    paused = client.post(f"{url}/pause", json={},
        headers=headers(csrf, str(uuid4()), revised.headers["ETag"]))
    assert paused.status_code == 200 and paused.json()["work"]["status"] == "paused"
    resumed = client.post(f"{url}/resume", json={},
        headers=headers(csrf, str(uuid4()), paused.headers["ETag"]))
    assert resumed.status_code == 200 and resumed.json()["work"]["status"] == "active"
    stopped = client.post(f"{url}/stop", json={},
        headers=headers(csrf, str(uuid4()), resumed.headers["ETag"]))
    assert stopped.status_code == 200 and stopped.json()["work"]["status"] == "stopped"

    other = client_factory()
    login(other, "work-other")
    assert other.get(url).status_code == 404
    assert other.get(f"{url}/events").status_code == 404
    assert other.get(f"{url}/runs").status_code == 404


def test_progress_projection_and_http_advance_gate(seed_identity, client_factory, db):
    seed_identity('progress-owner')
    seed_identity('progress-other')
    client = client_factory()
    csrf = login(client, 'progress-owner')
    response = client.post('/api/v1/works', json={
        'title': 'Bounded goal', 'requirement': {
            'objective': 'Bounded goal', 'capability_key': 'generic_work', 'spec': {'schema_version': 1},
        },
    }, headers=headers(csrf, str(uuid4())))
    assert response.status_code == 201
    work = response.json()['work']
    work_id = work['work_id']
    assert work['progress'] is None and work['daily_baseline'] is None
    # Migration-role fixture injects an already established protection fact;
    # production writes are verified with worker-role complex calls in test_progress.
    db.execute('INSERT INTO work_progress(account_id,work_id,requirement_revision,failure_limit,'
               'no_progress_limit,no_progress_count,blocked_reason) VALUES (%s,%s,1,3,3,3,%s)',
               (work['account_id'], work_id, 'no_progress'))
    url = f'/api/v1/works/{work_id}'
    current = client.get(url)
    assert current.json()['work']['progress']['blocked_reason'] == 'no_progress'
    rejected = client.post(url + '/advance', json={},
                           headers=headers(csrf, str(uuid4()), current.headers['ETag']))
    assert rejected.status_code == 409
    assert client.get(url + '/runs').json()['items'] == []
    login(client, 'progress-other')
    assert client.get(url).status_code == 404


def test_delivery_format_http_revision_replay_and_rejected_binding(seed_identity, client_factory, db):
    from test_delivery_contract_unit import requirement as delivery_requirement
    seed_identity('format-contract-owner')
    client = client_factory()
    csrf = login(client, 'format-contract-owner')
    original = delivery_requirement().to_dict()
    key = str(uuid4())
    payload = {'title': 'Markdown', 'requirement': original}
    response = client.post('/api/v1/works', json=payload, headers=headers(csrf, key))
    assert response.status_code == 201, response.text
    assert response.json()['work']['requirement']['deliverables'] == original['deliverables']
    replay = client.post('/api/v1/works', json=payload, headers=headers(csrf, key))
    assert replay.status_code == 201 and replay.headers['Idempotency-Replayed'] == 'true'
    assert replay.json() == response.json()
    wid = response.json()['work']['work_id']
    url = f'/api/v1/works/{wid}/revisions'
    revised = delivery_requirement(('docx',)).to_dict()
    bad = delivery_requirement().to_dict()
    bad['deliverables']['items'][0]['format_criterion_id'] = 'output0'
    denied = client.post(url, json={'requirement': bad}, headers=headers(csrf, str(uuid4()), response.headers['ETag']))
    assert denied.status_code == 422, denied.text
    accepted = client.post(url, json={'requirement': revised, 'change_reason': 'Word requested'},
                          headers=headers(csrf, str(uuid4()), response.headers['ETag']))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['work']['requirement']['deliverables'] == revised['deliverables']
    assert accepted.json()['work']['current_requirement_revision'] == 2
