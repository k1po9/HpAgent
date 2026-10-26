"""Initialize only trusted Accounts on a fresh, explicitly isolated P5 target."""
import json
import os
import subprocess
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest

from memory.hindsight_client import HindsightClient, HindsightRejected

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('P5_PREPARATION_ISOLATED') != '1', reason='requires P5 isolated empty target')]


async def test_empty_bank_initializer_active_idempotent_and_fail_closed(db, account_id, tmp_path):
    assert os.environ['HINDSIGHT_URL'] == 'http://hindsight:8888'
    disabled = uuid4()
    db.execute("INSERT INTO accounts(account_id,status) VALUES(%s,'disabled')", (disabled,))
    command = [sys.executable,'scripts/operations/memory-bank-initialize.py',
               '--senders-stopped','--config','config/hindsight.pgroonga.yaml']

    def invoke():
        return subprocess.run(command, capture_output=True, text=True)

    first = invoke()
    assert first.returncode == 0, first.stdout
    assert json.loads(first.stdout)['verified_banks'] == 1
    second = invoke()
    assert second.returncode == 0
    assert json.loads(second.stdout)['profile_hash'] == json.loads(first.stdout)['profile_hash']
    remote = HindsightClient(os.environ['HINDSIGHT_URL'])
    with pytest.raises(HindsightRejected) as missing:
        await remote.get_bank_config(str(disabled))
    assert missing.value.status == 404
    await remote.update_bank_config(str(account_id), {'enable_text_search': False})
    try:
        denied = invoke()
        assert denied.returncode == 2
        assert (await remote.get_bank_config(str(account_id)))['config']['enable_text_search'] is False
    finally:
        await remote.update_bank_config(str(account_id), {'enable_text_search': True})
    # Synthetic admin is confined to this dedicated memory fixture, not app roles.
    url = os.environ['P5_MEMORY_DATABASE_URL']
    target = urlsplit(url)
    assert target.hostname in {'postgres', 'recovery-postgres'}
    assert target.path == '/hindsight_110_pgroonga'
    doc = 'p5-unregistered-' + str(uuid4())
    with psycopg.connect(url, autocommit=True) as memory:
        memory.execute('INSERT INTO documents(id,bank_id) VALUES(%s,%s)',
                       (doc, 'hpagent-u-' + str(account_id)))
        try:
            assert invoke().returncode == 2
        finally:
            memory.execute('DELETE FROM documents WHERE id=%s AND bank_id=%s',
                           (doc, 'hpagent-u-' + str(account_id)))
    assert invoke().returncode == 0
    # Actual source drift: trailing LF is semantically inert, but the Adapter's
    # profile comparison is exact. A rollback must explicitly maintain it.
    from memory.quality_config import MemoryQualityConfig, load_memory_prompts

    quality = MemoryQualityConfig.from_yaml('config/hindsight.pgroonga.yaml')
    updates = quality.bank_updates(load_memory_prompts('config/prompts/memory.yaml'))
    drift = {key: updates[key] + '\n' for key in ('retain_mission', 'observations_mission')}
    await remote.update_bank_config(str(account_id), drift)
    assert invoke().returncode == 2
    backup = tmp_path / 'bank-before-canonical.json'
    applied = subprocess.run(
        [sys.executable, 'scripts/operations/memory-config.py', 'apply',
         '--config', 'config/hindsight.pgroonga.yaml', '--senders-stopped', '--backup', str(backup)],
        capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert backup.stat().st_mode & 0o777 == 0o600
    original = json.loads(backup.read_text())['banks'][str(account_id)]
    assert all(original[key] == value for key, value in drift.items())
    assert invoke().returncode == 0
