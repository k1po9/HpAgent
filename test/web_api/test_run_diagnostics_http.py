"""HTTP diagnostics use the authenticated owner and the API database role."""
from uuid import UUID, uuid4

import pytest

from conversation_domain.commands import CommandService

pytestmark = pytest.mark.postgres


def test_run_diagnostics_authentication_owner_and_no_input_leak(db, seed_identity, client_factory, database_url, worker_database_url):
    account = seed_identity('diagnostic-owner')
    seed_identity('diagnostic-other')
    commands = CommandService(database_url)
    conversation = UUID(commands.create_conversation(account, str(uuid4()))['conversation_id'])
    run = commands.send_message(account, conversation, str(uuid4()), 'PRIVATE-MODEL-INPUT')['run_id']
    from resources.model_client import ModelClient
    from resources.model_input_snapshot import SnapshotRepository

    db.execute("INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) VALUES (%s,'standard','none')", (account,))
    prepared = ModelClient({'api_key': 'PRIVATE-CREDENTIAL', 'base_url': 'https://example.test/v1',
                            'model': 'fixture', 'endpoint_id': 'fixture'}).prepare_request(
        [{'role': 'user', 'content': 'PRIVATE-MODEL-INPUT'}],
    )
    snapshot = SnapshotRepository(worker_database_url).create(
        account_id=account, run_id=UUID(run), model_call_id=uuid4(), operation_id='frozen-model-operation',
        execution_attempt=2, call_ordinal=1, fallback_attempt=3, phase='decision',
        entitlement_version=1, prepared=prepared,
    )
    client = client_factory()
    path = f'/api/v1/runs/{run}/diagnostics'
    assert client.get(path).status_code == 401
    response = client.post('/auth/login', json={
        'username': 'diagnostic-owner', 'password': 'correct-password', 'return_to': '/',
    }, follow_redirects=False)
    assert response.status_code == 303
    response = client.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body['run']['run_id'] == run
    assert body['operations'] == {'items': [], 'truncated': False}
    assert 'PRIVATE-' not in response.text
    model = body['model_attempts']['items'][0]
    assert model['snapshot_id'] == str(snapshot.snapshot_id)
    assert model['operation_id'] == 'frozen-model-operation'
    assert (model['execution_attempt'], model['fallback_attempt'], model['phase']) == (2, 3, 'decision')
    assert model['content_hash'] == snapshot.content_hash.hex()
    assert model['dispatched_at'] is None  # a snapshot is not evidence of dispatch
    other = client_factory()
    other.post('/auth/login', json={
        'username': 'diagnostic-other', 'password': 'correct-password', 'return_to': '/',
    }, follow_redirects=False)
    assert other.get(path).status_code == 404
    assert client.get(f'/api/v1/runs/{uuid4()}/diagnostics').status_code == 404
