"""Readiness uses real source/action/outbox facts, without changing them."""
import pytest
from test_memory_readiness import Remote as InventoryRemote

from application.memory_readiness import inspect_memory

from .test_memory_retention import setup_action, tick

pytestmark = pytest.mark.postgres


async def test_readiness_tracks_source_verification_and_scope(
    db, database_url, worker_database_url, account_id,
):
    service, outbox, remote, _ = await setup_action(
        db, database_url, worker_database_url, account_id)
    doc = db.execute('SELECT document_id FROM memory_sources').fetchone()[0]

    class Inventory(InventoryRemote):
        async def banks(self, **kwargs):
            return {'banks': [{'bank_id': 'hpagent-u-' + str(account_id)}], 'total': 1}

        async def get_document(self, account, document):
            detail = await remote.get_document(account, document)
            if self.wrong_scope:
                detail['tags'] = ['scope:group', 'group:other']
            return detail

    inventory = Inventory()
    inventory.wrong_scope = False
    inventory.documents = [{'id': doc}]
    initial = await inspect_memory(worker_database_url, inventory)
    assert not initial['data_ready']
    assert initial['problems']['source_unverified'] == 1
    await tick(db, outbox, service)
    for operation in remote.operations.values():
        operation['status'] = 'completed'
    await tick(db, outbox, service)
    epoch = db.execute('SELECT memory_epoch FROM accounts').fetchone()[0]
    assert (await inspect_memory(worker_database_url, inventory))['data_ready']
    assert not (await inspect_memory(worker_database_url, inventory, require_empty=True))['data_ready']
    inventory.wrong_scope = True
    result = await inspect_memory(worker_database_url, inventory)
    assert result['problems'] == {'scope_mismatch': 1}
    inventory.documents = []
    assert (await inspect_memory(worker_database_url, inventory))['problems'] == {
        'registered_document_missing': 1}
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == epoch
    assert db.execute('SELECT state FROM memory_sources').fetchone()[0] == 'active'
    # An upgrade can discover a historical denial while the old source still
    # says active. Both readiness and actual recall permission must reject it.
    db.execute('UPDATE runs SET memory_retention_blocked=true')
    inventory.documents = [{'id': doc}]
    inventory.wrong_scope = False
    assert (await inspect_memory(worker_database_url, inventory))['problems'] == {
        'source_permission_revoked': 1}
    assert not service.store.readable_documents(account_id, ['scope:private'], {doc})


async def test_verified_action_with_unknown_sender_is_not_drained(
    db, database_url, worker_database_url, account_id,
):
    from application.memory_readiness import local_inventory

    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id)
    await tick(db, outbox, service)
    for operation in remote.operations.values():
        operation['status'] = 'completed'
    await tick(db, outbox, service)
    db.execute("UPDATE memory_actions SET send_state='unknown' WHERE status='verified'")
    _, actions, _ = local_inventory(worker_database_url)
    assert actions == {'verified': 1}
