"""Empty-target rehearsal uses only fresh P4 restore services and a dedicated app DB."""
import json
import os
from pathlib import Path

import pytest

from application.memory_readiness import inspect_memory
from memory.hindsight_client import HindsightClient
from memory.quality_config import MemoryQualityConfig, load_memory_prompts

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('P4_ROUTE_ISOLATED') != '1', reason='requires fresh P4 empty-target fixture')]


async def test_empty_target_initialize_switch_and_no_write_rollback(db, worker_database_url, account_id):
    assert 'hpagent-pgroonga-p4-app-restore-1' in worker_database_url
    root = Path(__file__).resolve().parents[2]
    quality = MemoryQualityConfig.from_yaml(root / 'config/hindsight.yaml')
    prompts = load_memory_prompts(root / 'config/prompts/memory.yaml')
    clients = {}
    report = {}
    for arm in ['native', 'candidate']:
        url = os.environ['P4_' + arm.upper() + '_URL']
        assert url == f'http://{arm}-api:8888'
        remote = HindsightClient(url, quality_config=quality, memory_prompts=prompts)
        before = await inspect_memory(worker_database_url, remote, require_empty=True)
        assert before['data_ready']
        await remote.ensure_bank_config(str(account_id))
        config = await remote.get_bank_config(str(account_id))
        assert remote.config_matches(config, quality.bank_updates(prompts))
        after = await inspect_memory(worker_database_url, remote, require_empty=True)
        assert after['data_ready']
        clients[arm] = remote
        report[arm] = dict(empty_before=True,empty_after=True,bank_config_matches=True)
    # Adapter selection is the rehearsal's route; no deployment env/router changed.
    for arm in ['native', 'candidate', 'native']:
        assert await clients[arm].recall_candidates('紫砂茶壶 p4 empty control', str(account_id), scope='private') == []
    assert db.execute('SELECT count(*) FROM memory_sources').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM memory_actions').fetchone()[0] == 0
    report['route'] = ['native','candidate','native']
    output = os.getenv('P4_EMPTY_ROUTE_EVIDENCE')
    if output:
        Path(output).write_text(json.dumps(report,indent=2)+'\n')
