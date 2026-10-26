"""Persistent synthetic smoke for the explicitly isolated native/PGroonga fixture."""
import json
import os

import pytest
from test_hindsight_contract import eventually

from memory.hindsight_client import HindsightClient

pytestmark = pytest.mark.skipif(
    os.getenv('HINDSIGHT_CONTRACT_ISOLATED') != '1',
    reason='requires dedicated fixture and explicit opt-in',
)


async def test_pgroonga_persistent_retain_recall_and_consolidation():
    url = os.environ['HINDSIGHT_CONTRACT_URL']
    assert url in {'http://hindsight:8888', 'http://127.0.0.1:18889', 'http://127.0.0.1:18890'}
    adapter = HindsightClient(url, retain_timeout=60, recall_timeout=30)
    account = 'p1-persistence'
    receipt = await adapter.retain_document(
        [{'role': 'user', 'content': 'P1 member prefers violet notebooks and morning tea.'}],
        account, 'p1-persistent-document', scope='private',
        iso_timestamp='2026-10-26T00:00:00Z', async_retain=True,
        operation_id='e00fd585-a7c0-4472-8735-33ad31ab2dd2',
    )
    assert receipt.accepted and receipt.async_processing
    assert receipt.operation_id == 'e00fd585-a7c0-4472-8735-33ad31ab2dd2'

    async def completed():
        result = await adapter.get_operation(account, receipt.operation_id)
        assert result['status'] not in {'failed', 'cancelled'}
        return result['status'] == 'completed'

    await eventually(completed)

    async def consolidated():
        items = await adapter.recall_candidates('violet notebooks morning tea', account,
                                                scope='private')
        return items if any(i.memory_type == 'observation' for i in items) else None

    items = await eventually(consolidated)
    assert all(i.bank_id == 'hpagent-u-p1-persistence' for i in items)
    observations = [i for i in items if i.memory_type == 'observation']
    assert all(i.source_facts for i in observations)
    assert all(f['document_id'] == 'p1-persistent-document'
               for i in observations for f in i.source_facts)
    report = {
        'accepted': receipt.accepted,
        'operation_completed': True,
        'consolidation_observation_count': len(observations),
        'recall_result_count': len(items),
        'all_observation_sources_verified': True,
    }
    output = os.getenv('PGROONGA_SMOKE_RESULT')
    if output:
        from pathlib import Path
        Path(output).write_text(json.dumps(report, indent=2) + '\n')
