"""Cutover cannot mistake pending work or an unmapped bank for an empty target."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from application import memory_readiness
from memory.hindsight_client import HindsightContractError
from memory.scope_policy import memory_scope
from orchestration.config import AppConfig


class Remote:
    def __init__(self):
        self.documents = []
        self.operations = 0

    @asynccontextmanager
    async def _sdk(self):
        yield SimpleNamespace(aget_version=self.version,
            banks=SimpleNamespace(list_banks=self.banks),
            documents=SimpleNamespace(list_documents=self.docs),
            operations=SimpleNamespace(list_operations=self.ops))

    async def version(self):
        return SimpleNamespace(api_version='1.1.0')

    async def banks(self, **kwargs):
        return {'banks': [{'bank_id': 'unknown-bank'}], 'total': 1}

    async def docs(self, **kwargs):
        return {'items': self.documents, 'total': len(self.documents)}

    async def ops(self, bank_id, *, status, **kwargs):
        return {'total': self.operations if status == 'processing' else 0}


async def test_empty_check_is_read_only_and_unknown_documents_remain_isolated(monkeypatch):
    monkeypatch.setattr(memory_readiness, 'local_inventory', lambda _: ([], {}, {}))
    remote = Remote()
    first = await memory_readiness.inspect_memory('unused', remote, require_empty=True)
    assert first['data_ready'] and not first['business_switch_executed']
    remote.documents = [{'id': 'not-a-run', 'original_text': 'must not enter report'}]
    second = await memory_readiness.inspect_memory('unused', remote, require_empty=True)
    assert not second['data_ready']
    assert second['problems']['unregistered_isolated'] == 1
    assert second['problems']['empty_target_required'] == 1
    assert 'must not enter report' not in str(second)


async def test_check_rejects_local_and_remote_inflight_work(monkeypatch):
    monkeypatch.setattr(memory_readiness, 'local_inventory', lambda _: (
        [], {'uncertain': 1}, {'processing': 1}))
    remote = Remote()
    remote.operations = 2
    result = await memory_readiness.inspect_memory('unused', remote)
    assert not result['data_ready']
    assert result['remote_operations_requiring_reconciliation'] == 2
    assert result['actions_requiring_reconciliation'] == {'uncertain': 1}


async def test_inventory_visits_every_page_and_rejects_truncation():
    offsets = []

    async def fetch(offset, **kwargs):
        offsets.append(offset)
        return {'items': [{'id': str(offset)}] if offset < 2 else [], 'total': 2}

    assert [d['id'] async for d in memory_readiness.pages(fetch, 'items')] == ['0', '1']
    assert offsets == [0, 1]

    async def truncated(**kwargs):
        return {'items': [], 'total': 1}

    with pytest.raises(HindsightContractError, match='memory_inventory_incomplete'):
        _ = [d async for d in memory_readiness.pages(truncated, 'items')]


def test_recall_requires_explicit_configuration():
    config = AppConfig()
    assert not config.hindsight.recall_enabled
    config._apply_env_overrides({'HINDSIGHT_RECALL_ENABLED': 'true'})
    assert config.hindsight.recall_enabled
    config._apply_env_overrides({'HINDSIGHT_RECALL_ENABLED': 'false'})
    assert not config.hindsight.recall_enabled
    with pytest.raises(ValueError, match='HINDSIGHT_RECALL_ENABLED'):
        config._apply_env_overrides({'HINDSIGHT_RECALL_ENABLED': 'treu'})


def test_document_scope_is_unordered_but_never_accepts_extra_or_missing_ranges():
    policy = memory_scope('account', 'group', 'A')
    document = {'tags': ['group:A', 'scope:group'],
                'observation_scopes': [['group:A', 'scope:group']]}
    assert policy.matches_document(document)
    document['observation_scopes'].append(['scope:private'])
    assert not policy.matches_document(document)
    document['observation_scopes'] = []
    assert not policy.matches_document(document)
    document['observation_scopes'] = [['scope:group', 'group:B']]
    assert not policy.matches_document(document)
