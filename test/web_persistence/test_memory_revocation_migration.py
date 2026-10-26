"""Upgrade an isolated 077 fixture without losing a pre-registration denial."""
import shutil
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from application.memory_retention import MemoryRetentionService
from persistence.migrate import get_migrations_dir, migrate, verify_schema
from web_domain.outbox import OutboxService

from .test_memory_governance import GovernanceRemote, resource_grant
from .test_memory_retention import _completed_run

pytestmark = pytest.mark.postgres


async def test_upgrade_and_repeat_preserve_delayed_retention_denial(
    migration_database_url, database_url, worker_database_url, tmp_path, monkeypatch,
):
    name = 'memory_revoke_upgrade_' + uuid4().hex

    def target(url):
        params = conninfo_to_dict(url)
        params['dbname'] = name
        return make_conninfo(**params)

    owner, api, worker = map(target, (migration_database_url, database_url, worker_database_url))
    migrations = get_migrations_dir()
    previous = tmp_path / 'migrations-077'
    previous.mkdir()
    for path in migrations.glob('*.sql'):
        if path.name < '078':
            shutil.copyfile(path, previous / path.name)
    with psycopg.connect(migration_database_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            with monkeypatch.context() as old:
                old.setenv('HPAGENT_MIGRATIONS_DIR', str(previous))
                migrate(owner)
                account = uuid4()
                with psycopg.connect(owner, autocommit=True) as connection:
                    connection.execute('INSERT INTO hpagent.accounts(account_id) VALUES (%s)', (account,))
                conversation, run = _completed_run(api, worker, account)
                _, grant, _ = resource_grant(api, account, conversation)
                # Recorded by the previous release, before there was any source.
                with psycopg.connect(owner, autocommit=True) as connection:
                    connection.execute('UPDATE hpagent.resource_grants SET revoked_at=clock_timestamp() '
                                       'WHERE grant_id=%s', (grant,))
            migrate(owner)
            verify_schema(owner)
            migrate(owner)  # checksum-preserving replay, no second business effect
            with psycopg.connect(owner) as connection:
                assert connection.execute('SELECT memory_retention_blocked FROM hpagent.runs '
                                          'WHERE run_id=%s', (run,)).fetchone()[0]
            remote = GovernanceRemote()
            service = MemoryRetentionService(worker, remote)
            outbox = OutboxService(worker)
            for event in outbox.claim('upgrade-test', {'retain_memory'}, 10):
                await service.process_event(event, 'upgrade-test')
            assert not remote.calls
            with psycopg.connect(owner) as connection:
                assert connection.execute('SELECT state FROM hpagent.memory_sources').fetchone()[0] == 'quarantined'
                assert connection.execute('SELECT count(*) FROM hpagent.memory_actions').fetchone()[0] == 0
        finally:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
