"""Operations-only real backend checks on explicitly named synthetic fixtures."""
import copy
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from memory.hindsight_client import HindsightClient, HindsightUnavailable
from memory.quality_config import MemoryQualityConfig, load_memory_prompts
from memory.service_readiness import inspect_service

pytestmark = pytest.mark.skipif(os.getenv('HPAGENT_SERVICE_CHECK_ISOLATED') != '1',
                               reason='requires Docker operations authority and isolated services')
ROOT = Path(__file__).resolve().parents[1]


def settings():
    name = os.environ['PGROONGA_CHECK_ARM']
    assert name in {'native', 'candidate'}
    quality = MemoryQualityConfig.from_yaml(ROOT / 'config/hindsight.yaml')
    raw = quality.model_dump()
    raw['service']['text_search'] = {
        'extension': 'native' if name == 'native' else 'pgroonga', 'native_language': None}
    quality = MemoryQualityConfig.model_validate(raw)
    expected = {**quality.target.model_dump(), 'text_search': quality.text_search_settings().resolved({}),
                'target': {'host': 'postgres', 'port': 5432,
                           'database': 'pgroonga_' + name, 'schema': 'public'}}
    return name, quality, expected


async def test_real_effective_backend_and_explicit_bank_sync(tmp_path):
    name, quality, expected = settings()
    container = 'hpagent-pgroonga-' + name + '-hindsight-1'
    report = inspect_service(container, expected)
    assert report['service_ready'], report['problems']
    assert inspect_service(container, expected, api_url=os.environ['HINDSIGHT_CONTRACT_URL'])['service_ready']
    wrong = copy.deepcopy(expected)
    wrong['text_search']['extension'] = 'pgroonga' if name == 'native' else 'native'
    assert 'memory_service_backend_mismatch' in inspect_service(container, wrong)['problems']
    wrong = copy.deepcopy(expected)
    wrong['target']['database'] = 'unrelated'
    assert 'memory_service_target_mismatch' in inspect_service(container, wrong)['problems']
    wrong = copy.deepcopy(expected)
    wrong['target']['schema'] = 'unrelated'
    assert 'memory_service_schema_mismatch' in inspect_service(container, wrong)['problems']
    assert not inspect_service(container, expected, api_url='http://127.0.0.1:1')['service_ready']
    # This test has Docker authority solely for inspection; data changes use the supported SDK.
    remote = HindsightClient(os.environ['HINDSIGHT_CONTRACT_URL'], quality_config=quality,
                             memory_prompts=load_memory_prompts(ROOT / 'config/prompts/memory.yaml'))
    account = 'p2-' + str(uuid4())
    await remote.ensure_bank_config(account)  # new Bank initialized and read back
    try:
        await remote.update_bank_config(account, {'enable_text_search': False})
        rejected = inspect_service(container, expected)
        assert 'memory_service_bank_text_search_disabled' in rejected['problems']
        with pytest.raises(HindsightUnavailable, match='not_applied'):
            await remote.recall_candidates('synthetic query', account, scope='private')
        assert (await remote.get_bank_config(account))['config']['enable_text_search'] is False
    finally:
        await remote.update_bank_config(account, {'enable_text_search': True})
    assert inspect_service(container, expected)['service_ready']
    # Execute the actual CLI with a generated, non-secret fixture configuration.
    import yaml
    path = tmp_path / 'profile.yaml'
    path.write_text(yaml.safe_dump(quality.model_dump()))
    result = subprocess.run(['python', 'scripts/operations/memory-cutover-check.py',
        '--service-only', '--service-container', container, '--config', str(path),
        '--memory-db-host', 'postgres', '--memory-db-name', 'pgroonga_' + name],
        text=True, capture_output=True, timeout=40)
    assert result.returncode == 0, result.stdout
    cli = json.loads(result.stdout)
    assert cli['service_ready'] and cli['observed']['read_only'] == 'on'
    output = os.getenv('PGROONGA_EFFECTIVE_RESULT')
    if output:
        Path(output).write_text(json.dumps({'positive': cli, 'bank_closed_rejected': True,
            'existing_bank_was_not_auto_patched': True, 'new_bank_initialized': True,
            'target_and_backend_negatives': True}, indent=2) + '\n')
