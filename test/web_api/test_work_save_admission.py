"""Authenticated save requirements wait before execution; invalid writes are atomic."""
from uuid import UUID, uuid4

import pytest
from test_work_foundation import headers, login

from workspace.catalog import WorkspaceCatalog

pytestmark = pytest.mark.postgres


def test_invalid_save_target_and_pending_destination_have_distinct_http_results(seed_identity, client_factory, db, database_url):
    account = seed_identity('save-owner')
    client = client_factory()
    csrf, key = login(client, 'save-owner'), str(uuid4())
    requirement = {
        'objective': 'Publish and save Markdown', 'capability_key': 'generic_work',
        'spec': {'schema_version': 1},
        'acceptance_criteria': [
            {'id': 'text', 'required': True, 'evidence_types': ['output_exists']},
            {'id': 'save', 'required': True, 'evidence_types': ['workspace_saved']},
        ],
        'deliverable_policy': {'schema_version': 1, 'required': False,
                               'directory_id': '00000000-0000-0000-0000-000000000000'},
    }
    rejected = client.post('/api/v1/works', json={'title': 'Save', 'requirement': requirement}, headers=headers(csrf, key))
    assert rejected.status_code == 422
    assert rejected.json()['error']['code'] == 'workspace_save_invalid'
    assert rejected.json()['error']['retryable'] is False
    assert db.execute('SELECT count(*) FROM works WHERE account_id=%s', (account,)).fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM idempotency_commands WHERE account_id=%s AND idempotency_key=%s', (account, key)).fetchone()[0] == 0
    requirement['deliverable_policy'] = {'schema_version': 1, 'required': False}
    pending = client.post('/api/v1/works', json={'title': 'Save', 'requirement': requirement}, headers=headers(csrf, key))
    assert pending.status_code == 201
    w = pending.json()['work']
    assert w['continuation']['reason'] == 'workspace_save_destination_required'
    url = f"/api/v1/works/{w['work_id']}"
    assert client.post('/api/v1/works', json={'title': 'Save', 'requirement': requirement}, headers=headers(csrf, key)).json() == pending.json()
    root = WorkspaceCatalog(database_url).initialize(account)['root_id']
    revision_key = str(uuid4())
    invalid_requirement = {**requirement, 'deliverable_policy': {
        'schema_version': 1, 'required': False, 'directory_id': '00000000-0000-0000-0000-000000000000'}}
    invalid_revision = client.post(url + '/revisions', json={'requirement': invalid_requirement, 'change_reason': 'Choose directory'},
                                   headers=headers(csrf, revision_key, pending.headers['ETag']))
    assert invalid_revision.status_code == 422
    assert client.get(url).json() == pending.json()
    requirement['deliverable_policy'] = {'schema_version': 1, 'required': True, 'directory_id': root}
    revision = client.post(url + '/revisions', json={'requirement': requirement, 'change_reason': 'Choose directory'},
                           headers=headers(csrf, revision_key, pending.headers['ETag']))
    assert revision.status_code == 200
    w = revision.json()['work']
    assert w['continuation']['reason'] == 'workspace_save_permission_required'
    assert w['current_requirement_revision'] == 2
    assert db.execute('SELECT count(*) FROM runs WHERE work_id=%s', (UUID(w['work_id']),)).fetchone()[0] == 0
    granted = client.post(url + '/resources', json={'node_id': root, 'operations': ['create_child'], 'recursive': False},
                          headers=headers(csrf, str(uuid4()), revision.headers['ETag']))
    assert granted.status_code == 201, granted.text
    assert granted.json()['work']['continuation']['kind'] == 'ready'
