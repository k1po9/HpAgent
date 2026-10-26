from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from test_phase_b_api import command_headers, login
from web_persistence.test_memory_governance import GovernanceRemote
from web_persistence.test_memory_retention import tick
from web_persistence.test_phase_a_invariants import _conversation_and_run

from application.memory_retention import MemoryRetentionService
from conversation_domain.commands import CommandService
from web_domain.outbox import OutboxService

pytestmark = pytest.mark.postgres


def test_memory_api_auth_csrf_generation_idempotency_and_status(
    db, seed_identity, client_factory, database_url, worker_database_url,
):
    owner = seed_identity('alice')
    seed_identity('bob')
    _, _, run = _conversation_and_run(database_url, owner)
    worker = CommandService(worker_database_url)
    worker.start_run(owner, run)
    worker.complete_run(owner, run, 'answer')
    remote = GovernanceRemote()
    retention = MemoryRetentionService(worker_database_url, remote)
    outbox = OutboxService(worker_database_url)
    asyncio.run(tick(db, outbox, retention))
    asyncio.run(tick(db, outbox, retention))
    for op in remote.operations.values():
        op['status'] = 'completed'
    asyncio.run(tick(db, outbox, retention))
    source = str(db.execute('SELECT source_id FROM memory_sources').fetchone()[0])
    route = f'/api/v1/memory/sources/{source}/actions'
    alice, bob = client_factory(), client_factory()
    assert alice.get('/api/v1/memory/sources').status_code == 401
    csrf = login(alice)
    bob_csrf = login(bob, 'bob')
    assert alice.get('/api/v1/memory/sources').json()['sources'][0]['source_id'] == source
    detail = alice.get(f'/api/v1/memory/sources/{source}').json()
    assert detail['global_semantic_deletion'] is False
    assert 'request_payload' not in str(detail)
    payload = {'kind': 'correct', 'generation': 1, 'memory_id': remote.fact_id,
               'text': 'API correction body', 'reason': 'user correction'}
    assert alice.post(route, json=payload).status_code == 403
    assert alice.post(route, json=payload, headers=command_headers(csrf)).status_code == 422
    assert bob.post(route, json=payload, headers=command_headers(bob_csrf, str(uuid4()))).status_code == 404
    assert bob.get(f'/api/v1/memory/sources/{source}').status_code == 404
    assert alice.post(route, json={**payload, 'bank_id': 'untrusted'}, headers=command_headers(csrf, str(uuid4()))).status_code == 422
    assert alice.post(route, json={**payload, 'generation': 99}, headers=command_headers(csrf, str(uuid4()))).status_code == 412
    alice.app.state.memory_governance.remote = remote
    remote.facts[remote.fact_id]['tags'] = ['scope:group', 'group:B']
    assert alice.post(route, json=payload, headers=command_headers(csrf, str(uuid4()))).status_code == 403
    remote.facts[remote.fact_id]['tags'] = ['scope:private']
    key = str(uuid4())
    response = alice.post(route, json=payload, headers=command_headers(csrf, key))
    assert response.status_code == 202
    aid = response.json()['action_id']
    replay = alice.post(route, json=payload, headers=command_headers(csrf, key))
    assert replay.status_code == 202 and replay.headers['Idempotency-Replayed'] == 'true'
    assert replay.json() == response.json()
    assert alice.post(route, json={**payload, 'text': 'changed'}, headers=command_headers(csrf, key)).status_code == 409
    assert alice.get(f'/api/v1/memory/actions/{aid}').json()['action']['status'] == 'queued'
    assert bob.get(f'/api/v1/memory/actions/{aid}').status_code == 404
    asyncio.run(tick(db, outbox, retention))
    action = alice.get(f'/api/v1/memory/actions/{aid}').json()['action']
    assert action['status'] == 'verified' and 'request_payload' not in action
    receipt = db.execute("SELECT response_body::text FROM idempotency_commands WHERE operation='memory_action'").fetchone()[0]
    assert 'API correction body' not in receipt
    # Replay remains pinned to the original action even after ordinary receipts expire.
    db.execute("DELETE FROM idempotency_commands WHERE operation='memory_action'")
    assert alice.post(route, json=payload, headers=command_headers(csrf, key)).json()['action_id'] == aid
    # Archiving presentation does not remove the owner's governance access.
    db.execute("UPDATE conversations SET status='archived',archived_at=now() WHERE conversation_id=(SELECT conversation_id FROM runs WHERE run_id=%s)", (run,))
    assert alice.post(route, json=payload, headers=command_headers(csrf, key)).status_code == 202
    # A changed trusted scope is rechecked even for an old command key.
    db.execute("UPDATE messages SET origin='{\"channel_type\":\"qq\",\"scope\":\"group\",\"context_key\":\"B\"}' WHERE message_id=(SELECT trigger_message_id FROM runs WHERE run_id=%s)", (run,))
    assert alice.post(route, json=payload, headers=command_headers(csrf, key)).status_code == 404
