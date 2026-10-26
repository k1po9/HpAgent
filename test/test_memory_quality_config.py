"""Quality settings reach the actual SDK, context compiler, and service environment."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from aiohttp import web
from hindsight_client_api.models.features_info import FeaturesInfo
from test_hindsight import wire as wire

from application.context_assembly import ContextAssemblyService, WebContextBase
from application.context_builder import HarnessContextBuilder
from application.prompts import PromptLoader
from brain.engine import BrainEngine
from memory.configuration import configuration_diff, read_backup, write_backup
from memory.hindsight_client import HindsightUnavailable, MemoryItem
from memory.quality_config import MemoryConfigError, MemoryQualityConfig, load_memory_prompts
from orchestration.config import AppConfig, PromptsConfig

ROOT = Path(__file__).resolve().parents[1]


def profile():
    return MemoryQualityConfig.from_yaml(ROOT / 'config/hindsight.yaml')


def prompts():
    return load_memory_prompts(ROOT / 'config/prompts/memory.yaml')


def environment():
    return {'MINIMAX_API_BASE_URL': 'https://models.invalid/v1', 'MINIMAX_API_KEY': 'fixture-secret',
            'MINIMAX_STANDARD_MODEL': 'memory-model', 'SILICONFLOW_API_KEY': 'fixture-vector-secret',
            'SILICONFLOW_BASE_URL': 'https://vector.invalid/v1',
            'SILICONFLOW_EMBEDDING_MODEL': 'vector', 'SILICONFLOW_RERANK_MODEL': 'rank'}


@pytest.mark.parametrize('bad', [
    {'schema_version': True}, {'recall': {'top_n': True}}, {'recall': {'top_n': 0}}, {'recall': {'top_n': '5'}},
    {'recall': {'types': []}}, {'recall': {'budget': 'huge'}},
    {'recall': {'tags_match': 'any'}}, {'bank': {'default_observation_scopes': 'shared'}},
    {'bank': {'entity_labels': [{'key': 'person', 'tag': True}]}},
    {'bank': {'entity_labels': ['person']}},
    {'recall': {'min_scores': {'reranker': float('nan')}}},
    {'recall': {'source_facts': {'max_tokens': -1}}},
    {'reference_only': {'reflect': {'status': 'enabled'}}},
    {'service': {'api_key': 'secret-not-for-errors'}},
])
def test_invalid_settings_fail_without_values(tmp_path, bad):
    path = tmp_path / 'quality.yaml'
    path.write_text(yaml.safe_dump(bad))
    with pytest.raises(MemoryConfigError) as raised:
        MemoryQualityConfig.from_yaml(path)
    assert 'secret-not-for-errors' not in str(raised.value)


def test_explicit_legacy_limit_and_conflict(tmp_path):
    path = tmp_path / 'quality.yaml'
    path.write_text('{}')
    assert MemoryQualityConfig.from_yaml(path, legacy_top_n=7).recall.top_n == 7
    path.write_text('recall: {top_n: 2}')
    with pytest.raises(MemoryConfigError, match='conflicts'):
        MemoryQualityConfig.from_yaml(path, legacy_top_n=7)


def test_null_reset_prompt_consumption_and_profile_identity(tmp_path):
    quality = MemoryQualityConfig.model_validate({'bank': {'retain_chunk_size': None}})
    assert quality.bank_updates(prompts())['retain_chunk_size'] is None
    assert 'enable_observations' not in quality.bank_updates(prompts())
    custom = MemoryQualityConfig.model_validate({'bank': {'retain_extraction_mode': 'custom'}})
    with pytest.raises(MemoryConfigError, match='retain_custom_instructions'):
        custom.bank_updates(prompts())
    cfg = AppConfig.from_yaml(ROOT / 'config/config.yaml')
    loader = PromptLoader(cfg.prompts)
    assert loader.get_memory('observations_mission') == prompts()['observations_mission']
    assert not loader.get_system('recall_instruction')
    before = profile().snapshot(prompts())
    changed = {**prompts(), 'retain_mission': 'Changed extraction purpose.'}
    assert before['config_hash'] != profile().snapshot(changed)['config_hash']


def test_service_routes_and_explicit_legacy_overrides():
    env = environment()
    result = profile().service_environment(env)
    assert result['HINDSIGHT_API_LLM_MODEL'] == 'memory-model'
    assert result['HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL'] == 'vector'
    assert result['HINDSIGHT_API_RERANKER_SILICONFLOW_MODEL'] == 'rank'
    assert result['HINDSIGHT_API_LLM_API_KEY'] == 'fixture-secret'
    env['HINDSIGHT_API_LLM_MODEL'] = 'explicit-override'
    assert profile().service_environment(env)['HINDSIGHT_API_LLM_MODEL'] == 'explicit-override'
    env.pop('SILICONFLOW_API_KEY')
    with pytest.raises(MemoryConfigError) as raised:
        profile().service_environment(env)
    assert 'fixture-secret' not in str(raised.value)


def test_backup_only_managed_fields_and_secret_safe_plan(tmp_path):
    state = {'config': {'retain_mission': 'private-profile', 'retain_chunk_size': 3000},
             'overrides': {'retain_mission': 'private-profile', 'unmanaged': 'keep'}}
    updates = {'retain_mission': 'new-profile', 'retain_chunk_size': 4000}
    assert 'private-profile' not in json.dumps(configuration_diff(state, updates))
    backup = tmp_path / 'original.json'
    write_backup(backup, {'u': state}, updates)
    assert backup.stat().st_mode & 0o777 == 0o600
    assert read_backup(backup) == {'u': {'retain_mission': 'private-profile', 'retain_chunk_size': None}}
    with pytest.raises(FileExistsError):
        write_backup(backup, {'u': state}, updates)


async def test_bank_sync_requires_explicit_apply_and_rechecks_after_change(wire):
    client, calls, reply = wire
    client.quality_config = profile()
    client._memory_prompts = prompts()
    expected = client.quality_config.bank_updates(prompts())
    state = {'bank_id': 'hpagent-u-u', 'config': {}, 'overrides': {}}

    async def handler(request, body):
        if request.path == '/version':
            return web.json_response({'api_version': '1.1.0', 'features': {key: True for key in FeaturesInfo.model_fields}})
        if request.method == 'PATCH':
            state['config'].update(body['updates'])
            state['overrides'].update(body['updates'])
        return web.json_response(state)

    reply['handler'] = handler
    with pytest.raises(HindsightUnavailable, match='not_applied'):
        await client.ensure_bank_config('u')
    assert not any(method == 'PATCH' for method, _, _ in calls)
    await client.update_bank_config('u', expected)
    await client.ensure_bank_config('u')
    assert client._ready_configs
    count = len(calls)
    await client.ensure_bank_config('u')
    assert len(calls) == count
    client._memory_prompts = {**prompts(), 'retain_mission': 'new mission'}
    with pytest.raises(HindsightUnavailable, match='not_applied'):
        await client.ensure_bank_config('u')


async def test_configured_candidates_keep_budget_and_all_results(wire):
    client, calls, reply = wire
    client.quality_config = MemoryQualityConfig.model_validate({
        'recall': {'budget': 'high', 'max_tokens': 2048, 'top_n': 2,
                   'source_facts': {'max_tokens': 1024}}, 'diagnostics': {'trace': True}})
    async def ensured(*args, **kwargs):
        pass
    client.ensure_bank_config = ensured
    reply['body'] = {'results': [dict(id=str(i), text=str(i), type='world', document_id=str(i),
                                     tags=['scope:private']) for i in range(8)]}
    reply['body']['trace'] = {
        'query': {'text': 'private-query'},
        'summary': {'total_duration_seconds': 0.2, 'budget_used': 8, 'query': 'private-query',
                    'phase_metrics': [{'details': {'text': 'private-memory'}}]}}
    items = await client.recall_candidates('q', 'u', scope='private')
    assert len(items) == 8
    assert items.trace_summary == {'total_duration_seconds': 0.2, 'budget_used': 8}
    assert 'private-' not in json.dumps(items.trace_summary)
    body = calls[-1][2]
    assert 'top_n' not in body
    assert (body['budget'], body['max_tokens'], body['trace']) == ('high', 2048, True)
    assert body['include']['source_facts']['max_tokens'] == 1024
    assert body['tags_match'] == 'all_strict'
    assert len(await client.recall('q', 'u', scope='private', top_n=2)) == 2


async def test_snapshot_mismatch_never_sends(wire):
    client, calls, _ = wire
    with pytest.raises(HindsightUnavailable, match='snapshot_mismatch'):
        await client.retain_document([{'content': 'x'}], 'u', 'd', scope='private',
                                     quality_snapshot={'config_hash': 'old'})
    assert not calls


@pytest.mark.parametrize('top_n,allowed_count', [(5, 5), (2, 5), (7, 5), (5, 2)])
async def test_final_limit_after_permissions_and_selected_dependencies(monkeypatch, top_n, allowed_count):
    from application import context_assembly as module

    items = [MemoryItem(str(i), source_session_id=str(i), tags=('scope:private',)) for i in range(8)]
    captured = []
    class Remote:
        async def recall_candidates(self, *args, **kwargs):
            captured.append(kwargs)
            return items
    class Uow:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args): return self
        def fetchone(self): return {'memory_context': {'epoch': 0, 'scopes': [], 'documents': []}}
    monkeypatch.setattr(module, 'UnitOfWork', lambda _: Uow())
    allowed = {str(i) for i in range(3, 3 + allowed_count)}
    monkeypatch.setattr(module.MemoryStore, 'readable_documents', lambda *a: allowed)
    monkeypatch.setattr('run_domain.execution.assert_execution_fence', lambda _: None)
    monkeypatch.setattr('persistence.memory_context.check_manifest', lambda *a: None)
    bound = []
    monkeypatch.setattr('persistence.memory_context.bind_context', lambda *a: bound.append(set(a[4])))
    service = ContextAssemblyService('unused', HarnessContextBuilder(), Remote(), recall_top_n=top_n,
                                     recall_instruction='Use this historical evidence carefully.')
    monkeypatch.setattr(service, 'bind_base', lambda _: None)
    base = WebContextBase(run_id=uuid4(), account_id=uuid4(), conversation_id=uuid4(), session_id=None,
                          execution_id=uuid4(), context_message_seq=1, trigger_message_id=uuid4(),
                          trigger_content='q', short_term_events=(), trigger_created_at='2026-10-26T00:00:00Z')
    selected = await service.recall_long_term(base, 'query')
    expected = items[3:3 + min(top_n, allowed_count)]
    assert list(selected) == expected
    assert bound == [{i.source_session_id for i in expected}]
    assert captured[0]['query_timestamp'] == base.trigger_created_at
    text = str(service.compose(base, selected))
    assert 'Use this historical evidence carefully.' in text
    assert 'Use this historical evidence carefully.' not in str(service.compose(base, ()))


async def test_original_or_disabled_recall_does_not_invoke_rewrite():
    class Model:
        async def generate(self, **kwargs):
            raise AssertionError('model must not be invoked')
    loader = PromptLoader(PromptsConfig(system={'hyde_rewrite': 'rewrite'}))
    for kwargs in ({'recall_query_mode': 'original'}, {'recall_enabled': False}):
        engine = BrainEngine(resource_pool=Model(), prompts=loader, **kwargs)
        assert await engine.rewrite_recall_query(user_content='original') == ('original', None)


async def test_actual_composition_receives_nondefault_limit(monkeypatch):
    import sys

    from application import context_assembly

    original_init = AppConfig.__init__
    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.memory_quality = MemoryQualityConfig.model_validate({'recall': {'top_n': 7}})
    monkeypatch.setattr(AppConfig, '__init__', initialize)
    original = context_assembly.ContextAssemblyService
    captured = []
    def compose(*args, **kwargs):
        service = original(*args, **kwargs)
        captured.append(service)
        return service
    monkeypatch.setattr(context_assembly, 'ContextAssemblyService', compose)
    sys.path.insert(0, str(ROOT / 'scripts/check'))
    try:
        from temporal_registry import capture_registries
        await capture_registries()
    finally:
        sys.path.pop(0)
    assert captured and captured[0]._recall_top_n == 7


def test_native_scores_do_not_replace_rank_relevance():
    item = MemoryItem.from_recall_result({'text': 'fact', 'scores': {
        'semantic': 0.8, 'keyword': None, 'reranker': 0.001, 'final': 0.7}})
    assert item.scores.final == 0.7 and item.scores.reranker == 0.001
    assert item.relevance == 1.0


async def test_lost_config_ack_is_reconciled_without_another_write(wire):
    from memory.hindsight_client import HindsightUncertain

    client, calls, reply = wire
    client.quality_config = profile()
    client._memory_prompts = prompts()
    state = {'bank_id': 'hpagent-u-u', 'config': {}, 'overrides': {}}
    async def handler(request, body):
        if request.path == '/version':
            return web.json_response({'api_version': '1.1.0',
                                      'features': {key: True for key in FeaturesInfo.model_fields}})
        if request.method == 'PATCH':
            state['config'].update(body['updates'])
            state['overrides'].update(body['updates'])
            return web.json_response({'detail': 'private body'}, status=503)
        return web.json_response(state)
    reply['handler'] = handler
    client._ready_configs.add(('old-bank', 'old-profile'))
    with pytest.raises(HindsightUncertain):
        await client.update_bank_config('u', profile().bank_updates(prompts()))
    assert not client._ready_configs
    await client.ensure_bank_config('u')
    assert len([call for call in calls if call[0] == 'PATCH']) == 1


def test_snapshot_tracks_model_selection_but_not_main_recall_instruction(monkeypatch):
    monkeypatch.setenv('MINIMAX_STANDARD_MODEL', 'first-model')
    first = profile().snapshot(prompts())
    instruction_only = {**prompts(), 'recall_instruction': 'A different Main instruction.'}
    assert profile().snapshot(instruction_only)['config_hash'] == first['config_hash']
    monkeypatch.setenv('MINIMAX_STANDARD_MODEL', 'second-model')
    second = profile().snapshot(prompts())
    assert second['config_hash'] != first['config_hash']
    assert second['model_identities']['llm']['model'] == 'second-model'
    assert second['service_remote_verified'] is False


async def test_frozen_profile_is_local_and_does_not_change_sdk_retain_shape(wire):
    client, calls, reply = wire
    client.quality_config = profile()
    client._memory_prompts = prompts()
    async def prepared(*args, **kwargs):
        pass
    client.ensure_bank_config = prepared
    operation = str(uuid4())
    reply['body'] = {'success': True, 'bank_id': 'hpagent-u-u', 'items_count': 1,
                     'async': True, 'operation_id': operation}
    receipt = await client.retain_document(
        [{'role': 'user', 'content': 'Explicit preference.'}], 'u', 'source', scope='private',
        iso_timestamp='2026-10-26T00:00:00Z', async_retain=True, operation_id=operation,
        quality_snapshot=client.quality_snapshot())
    assert receipt.operation_id == operation
    assert len(calls) == 1
    assert 'quality_snapshot' not in calls[0][2]
    assert calls[0][2]['items'][0]['content'] == '[user]: Explicit preference.'


def text_search_profile(backend='native', language=None):
    raw = profile().model_dump()
    raw['service']['text_search'] = {'extension': backend, 'native_language': language}
    return MemoryQualityConfig.model_validate(raw)


def test_keyword_defaults_compilation_and_environment_conflicts():
    quality = profile()
    raw = quality.model_dump()
    raw['service'].pop('text_search')
    default = MemoryQualityConfig.model_validate(raw)
    assert default.text_search_environment({}) == {
        'HINDSIGHT_API_TEXT_SEARCH_EXTENSION': 'native',
        'HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE': 'english'}
    pg = text_search_profile('pgroonga')
    assert pg.service_environment(environment())['HINDSIGHT_API_TEXT_SEARCH_EXTENSION'] == 'pgroonga'
    assert 'HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE' not in pg.service_environment(environment())
    assert text_search_profile(language='simple').text_search_environment({})[
        'HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE'] == 'simple'
    for config, env in [
        (quality, {'HINDSIGHT_API_TEXT_SEARCH_EXTENSION': 'pgroonga'}),
        (pg, {'HINDSIGHT_API_TEXT_SEARCH_EXTENSION': 'native'}),
        (pg, {'HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE': 'english'}),
        (quality, {'HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE': 'simple'}),
    ]:
        with pytest.raises(MemoryConfigError, match='conflict'):
            config.text_search_environment(env)
    assert pg.text_search_environment({'HINDSIGHT_API_TEXT_SEARCH_EXTENSION': 'pgroonga'})


@pytest.mark.parametrize('settings', [
    {'extension': 'secret-backend'}, {'extension': True}, {'extension': None},
    {'extension': 'pgroonga', 'native_language': 'chinese'},
    {'native_language': "english'; secret-value"}, {'native_language': ''},
    {'tokenizer': 'secret-tokenizer'},
])
def test_invalid_keyword_config_is_value_safe(tmp_path, settings):
    raw = profile().model_dump()
    raw['service']['text_search'] = settings
    path = tmp_path / 'invalid.yaml'
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(MemoryConfigError) as raised:
        MemoryQualityConfig.from_yaml(path)
    assert 'secret-' not in str(raised.value)


def test_keyword_effective_identity_is_frozen_but_not_remotely_verified(monkeypatch):
    monkeypatch.delenv('HINDSIGHT_API_TEXT_SEARCH_EXTENSION', raising=False)
    first = text_search_profile().snapshot(prompts())
    assert first['config_hash'] == text_search_profile(language='english').snapshot(prompts())['config_hash']
    changed = text_search_profile('pgroonga').snapshot(prompts())
    assert changed['config_hash'] != first['config_hash']
    assert changed['service_hash'] != first['service_hash']
    assert changed['text_search'] == {'extension': 'pgroonga', 'native_language': None}
    assert changed['target'] == profile().target.model_dump()
    assert changed['service_remote_verified'] is False


async def test_old_keyword_snapshot_rejects_before_network_without_rewriting(wire):
    client, calls, _ = wire
    client.quality_config = text_search_profile()
    client._memory_prompts = prompts()
    frozen = client.quality_snapshot()
    original = json.dumps(frozen, sort_keys=True)
    client.quality_config = text_search_profile('pgroonga')
    with pytest.raises(HindsightUnavailable, match='snapshot_mismatch'):
        await client.prepare_retain('u', frozen)
    assert not calls
    assert json.dumps(frozen, sort_keys=True) == original
