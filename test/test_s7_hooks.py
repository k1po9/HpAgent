"""Real boundary hooks with deterministic adapters; no external requests."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/acceptance'))
from s7 import worker
from s7.core import Ledger


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('s7_cli_fixture', ROOT / 'scripts/acceptance/s7_1.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, 'CAMPAIGN', tmp_path / 'campaign')
    monkeypatch.setattr(cli, 'image', lambda _: 'sha256:' + 'a' * 64)
    monkeypatch.setattr(cli, 'fingerprint', lambda: {k: 'fixture' for k in ('head', 'tree_hash', 'migration_hash', 'runtime_hash')})
    m = cli.create_manifest(tmp_path / 'batch', 11)
    from application.chat_execution import PostgresWebRequestLoader
    monkeypatch.setattr(PostgresWebRequestLoader, 'load', PostgresWebRequestLoader.load)
    monkeypatch.setattr(worker, 'BATCH', tmp_path)
    monkeypatch.setattr(worker, 'has_committed_html', lambda *_: True)
    monkeypatch.setattr(worker, 'target_work', lambda *_, **__: True)
    return m, Ledger(tmp_path / 'ledger.sqlite', m)


async def test_raw_html_fault_consumes_real_usage_then_original_validator_rejects(frozen, tmp_path, monkeypatch):
    import httpx

    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.segments import SegmentActivities
    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from resources import model_budget_context
    from resources.model_client import ModelClient
    from web_artifacts.generator import ArtifactGenerationError, WebArtifactGenerator
    monkeypatch.setattr(httpx.AsyncClient, 'post', httpx.AsyncClient.post)
    m, ledger = frozen
    sent = []
    async def adapter(*args, **kwargs):
        sent.append(1)
        return SimpleNamespace(content=None if len(sent) == 3 else '<html><body>valid original</body></html>', usage={'usage_source': 'provider', 'total_tokens': 25}, tool_calls=[])
    monkeypatch.setattr(ModelClient, 'send_prepared', adapter)
    # Register originals with monkeypatch so all composition substitutions unwind.
    monkeypatch.setattr(CompositeSourceDiscoveryProvider, 'discover', CompositeSourceDiscoveryProvider.discover)
    monkeypatch.setattr(StaticWebContentProvider, 'fetch', StaticWebContentProvider.fetch)
    monkeypatch.setattr(CapabilityActivities, 'close', CapabilityActivities.close)
    context = SimpleNamespace(snapshot_id='one', account_id='account', run_id='run', operation_id='produce', phase='artifact_generation')
    monkeypatch.setattr(model_budget_context, 'current_model_call', lambda: context)
    (tmp_path / 'active-sample').write_text('M3-1')
    monkeypatch.setattr(SegmentActivities, 'release', SegmentActivities.release)
    monkeypatch.setattr(ModelClient, 'before_dispatch', ModelClient.before_dispatch)
    worker.install_hooks(m, ledger)
    prepared = SimpleNamespace(verify_immutable=lambda: None, provider=m['model']['provider'], model=m['model']['id'],
        url=m['model']['base_url'] + '/chat/completions', body=lambda: {'messages': [], 'max_tokens': 4096, 'enable_thinking': False})
    await ModelClient.before_dispatch(object(), prepared)
    response = await ModelClient.send_prepared(object(), prepared)
    with pytest.raises(ArtifactGenerationError, match='完整 HTML'):
        WebArtifactGenerator(None).harden(response.content)
    assert ledger.summary()['measured_tokens'] == 25
    context.snapshot_id = 'two'
    await ModelClient.before_dispatch(object(), prepared)
    response = await ModelClient.send_prepared(object(), prepared)
    assert '</html>' in WebArtifactGenerator(None).harden(response.content)
    context.snapshot_id = 'three'
    context.phase = 'decision'
    await ModelClient.before_dispatch(object(), prepared)
    response = await ModelClient.send_prepared(object(), prepared)
    assert response.content is None
    assert ledger.summary()['requests'] == 3
    assert ledger.summary()['uncertain_requests'] == 0
    assert len(sent) == 3


async def test_committed_barrier_cancellation_holds_no_resource(frozen, tmp_path, monkeypatch):
    import httpx

    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.segments import SegmentActivities
    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from resources.model_client import ModelClient
    monkeypatch.setattr(httpx.AsyncClient, 'post', httpx.AsyncClient.post)
    m, ledger = frozen
    released = []
    async def closed(*_):
        released.append(True)
        return 'closed'
    monkeypatch.setattr(ModelClient, 'send_prepared', ModelClient.send_prepared)
    monkeypatch.setattr(CompositeSourceDiscoveryProvider, 'discover', CompositeSourceDiscoveryProvider.discover)
    monkeypatch.setattr(StaticWebContentProvider, 'fetch', StaticWebContentProvider.fetch)
    monkeypatch.setattr(CapabilityActivities, 'close', closed)
    (tmp_path / 'active-sample').write_text('M4-1')
    monkeypatch.setattr(SegmentActivities, 'release', SegmentActivities.release)
    monkeypatch.setattr(ModelClient, 'before_dispatch', ModelClient.before_dispatch)
    worker.install_hooks(m, ledger)
    child = SimpleNamespace(workflow_name='HtmlCapabilityWorkflow', run_id='run', invocation_id='invocation', execution_id='child', operation_id='produce')
    task = asyncio.create_task(CapabilityActivities.close(None, SimpleNamespace(child=child)))
    for _ in range(100):
        if (tmp_path / 'M4-1.barrier.json').exists():
            break
        await asyncio.sleep(0.01)
    assert released == [True]
    assert json.loads((tmp_path / 'M4-1.barrier.json').read_text())['invocation_id'] == 'invocation'
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The same consumed fault must not block a retried close after restart.
    assert await CapabilityActivities.close(None, SimpleNamespace(child=child)) == 'closed'


async def test_curated_official_docs_do_not_require_search_or_expand_egress(frozen, tmp_path, monkeypatch):
    import httpx

    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.segments import SegmentActivities
    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from research_domain.models import SourceStrategy
    from resources.model_client import ModelClient

    m, ledger = frozen
    monkeypatch.setattr(httpx.AsyncClient, 'post', httpx.AsyncClient.post)
    monkeypatch.setattr(ModelClient, 'send_prepared', ModelClient.send_prepared)
    monkeypatch.setattr(CapabilityActivities, 'close', CapabilityActivities.close)
    monkeypatch.setattr(CompositeSourceDiscoveryProvider, 'discover', CompositeSourceDiscoveryProvider.discover)
    monkeypatch.setattr(StaticWebContentProvider, 'fetch', StaticWebContentProvider.fetch)
    monkeypatch.setattr(SegmentActivities, 'release', SegmentActivities.release)
    monkeypatch.setattr(ModelClient, 'before_dispatch', ModelClient.before_dispatch)
    worker.install_hooks(m, ledger)
    provider = CompositeSourceDiscoveryProvider([])
    strategy = SourceStrategy(public_web=False, official_sources=True, github=False, rss=False)
    candidates = await provider.discover('asyncio', strategy=strategy, limit=3)
    assert [c.uri for c in candidates] == [s['url'] for s in m['sources']]
    assert not await provider.discover('asyncio', strategy=SourceStrategy(public_web=False, official_sources=False), limit=3)
    assert ledger.summary()['requests'] == 0


async def test_control_barrier_skips_insufficient_html_without_published_candidate(frozen, tmp_path, monkeypatch):
    import httpx

    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.segments import SegmentActivities
    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from resources.model_client import ModelClient

    m, ledger = frozen
    async def closed(*_):
        return 'closed'
    monkeypatch.setattr(httpx.AsyncClient, 'post', httpx.AsyncClient.post)
    monkeypatch.setattr(ModelClient, 'send_prepared', ModelClient.send_prepared)
    monkeypatch.setattr(CapabilityActivities, 'close', closed)
    monkeypatch.setattr(SegmentActivities, 'release', SegmentActivities.release)
    monkeypatch.setattr(CompositeSourceDiscoveryProvider, 'discover', CompositeSourceDiscoveryProvider.discover)
    monkeypatch.setattr(StaticWebContentProvider, 'fetch', StaticWebContentProvider.fetch)
    monkeypatch.setattr(worker, 'has_committed_html', lambda *_: False)
    (tmp_path / 'active-sample').write_text('M4-1')
    monkeypatch.setattr(ModelClient, 'before_dispatch', ModelClient.before_dispatch)
    worker.install_hooks(m, ledger)
    child = SimpleNamespace(workflow_name='HtmlCapabilityWorkflow')
    assert await CapabilityActivities.close(None, SimpleNamespace(child=child)) == 'closed'
    assert not (tmp_path / 'M4-1.barrier.json').exists()


async def test_html_fault_cannot_corrupt_a_chat_or_another_work(frozen, tmp_path, monkeypatch):
    import httpx

    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.segments import SegmentActivities
    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from resources import model_budget_context
    from resources.model_client import ModelClient

    m, ledger = frozen
    async def response(*_, **__):
        return SimpleNamespace(content='<html><body>complete</body></html>', usage={'usage_source': 'provider', 'total_tokens': 10}, tool_calls=[])
    monkeypatch.setattr(httpx.AsyncClient, 'post', httpx.AsyncClient.post)
    monkeypatch.setattr(ModelClient, 'send_prepared', response)
    monkeypatch.setattr(CapabilityActivities, 'close', CapabilityActivities.close)
    monkeypatch.setattr(SegmentActivities, 'release', SegmentActivities.release)
    monkeypatch.setattr(CompositeSourceDiscoveryProvider, 'discover', CompositeSourceDiscoveryProvider.discover)
    monkeypatch.setattr(StaticWebContentProvider, 'fetch', StaticWebContentProvider.fetch)
    monkeypatch.setattr(worker, 'target_work', lambda *_, **__: False)
    context = SimpleNamespace(snapshot_id='chat-call', account_id='account', run_id='chat', operation_id='produce', phase='artifact_generation')
    monkeypatch.setattr(model_budget_context, 'current_model_call', lambda: context)
    (tmp_path / 'active-sample').write_text('M3-1')
    monkeypatch.setattr(ModelClient, 'before_dispatch', ModelClient.before_dispatch)
    worker.install_hooks(m, ledger)
    prepared = SimpleNamespace(verify_immutable=lambda: None, provider=m['model']['provider'], model=m['model']['id'],
        url=m['model']['base_url'] + '/chat/completions', body=lambda: {'messages': [], 'max_tokens': 4096, 'enable_thinking': False})
    await ModelClient.before_dispatch(object(), prepared)
    result = await ModelClient.send_prepared(object(), prepared)
    assert '</html>' in result.content
    with ledger.connect() as db:
        assert db.execute('SELECT count(*) FROM faults').fetchone()[0] == 0


async def test_mvp_uses_actual_discovery_and_only_records_the_returned_candidates(frozen, tmp_path, monkeypatch):
    import httpx

    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.segments import SegmentActivities
    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from research_domain.models import SourceCandidate, SourceStrategy
    from resources.model_client import ModelClient
    m, ledger = frozen
    m['profile'] = 'research-html-mvp'
    calls = []

    class Search:
        async def discover(self, query, *, strategy, limit):
            calls.append(query)
            return [SourceCandidate(uri='https://developer.mozilla.org/actual-search-result', provider='searxng')]

    for cls, name in [(httpx.AsyncClient, 'post'), (ModelClient, 'send_prepared'),
                      (ModelClient, 'before_dispatch'), (CompositeSourceDiscoveryProvider, 'discover'),
                      (StaticWebContentProvider, 'fetch'), (CapabilityActivities, 'close'),
                      (SegmentActivities, 'release')]:
        monkeypatch.setattr(cls, name, getattr(cls, name))
    (tmp_path / 'active-sample').write_text('research_1')
    worker.install_hooks(m, ledger)
    results = await CompositeSourceDiscoveryProvider([Search()]).discover('actual user query', strategy=SourceStrategy(), limit=3)
    assert calls == ['actual user query']
    assert results[0].uri.endswith('actual-search-result')
    with ledger.connect() as db:
        event = json.loads(db.execute("SELECT body FROM events WHERE kind='source_discovered'").fetchone()[0])
    assert event['candidates'] == [{'uri': results[0].uri, 'provider': 'searxng'}]
    assert ledger.summary()['requests'] == 0


async def test_unpaid_source_gate_rejects_fetchable_homepage_without_topic_coverage(monkeypatch):
    from s7.mvp import source_precheck

    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from research_domain.models import SourceCandidate, SourceContent, content_sha256

    async def discover(self, query, **kwargs):
        return [SourceCandidate('https://developer.mozilla.org/en-US/'),
                SourceCandidate('https://developer.mozilla.org/en-US/docs/MDN/')]
    async def fetch(self, candidate):
        text = ('MDN provides CSS documentation and web development tutorials.\n\n' * 20)
        return SourceContent(candidate.uri, 'Home', text, content_sha256(text))
    monkeypatch.setattr(CompositeSourceDiscoveryProvider, 'discover', discover)
    monkeypatch.setattr(StaticWebContentProvider, 'fetch', fetch)
    result = await source_precheck({'tasks': [{'id': 'layout', 'objective': 'Compare Flexbox and Grid'}],
        'prompt': '{objective}. ' + 'Preserve conditions. ' * 25}, 'http://unused')
    assert result['model_requests'] == 0
    assert not result['passed']
    assert set(result['tasks'][0]['missing_concepts']) == {'flexbox', 'grid'}


def test_mvp_paid_entry_requires_current_passing_source_gate(tmp_path):
    from s7.core import Rejected
    spec = importlib.util.spec_from_file_location('s7_cli_gate', ROOT / 'scripts/acceptance/s7_1.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    manifest = {'profile': 'research-html-mvp', 'code': {'tree_hash': 'current'}}
    for value in ({}, {'gate_version': 2, 'passed': False},
                  {'gate_version': 2, 'passed': True, 'model_requests': 0, 'code_tree_hash': 'old',
                   'tasks': [{'passed': True}, {'passed': True}]}):
        (tmp_path / 'source-precheck.json').write_text(json.dumps(value))
        with pytest.raises(Rejected, match='unpaid source gate'):
            cli.require_source_precheck(tmp_path, manifest)
    (tmp_path / 'source-precheck.json').write_text(json.dumps({'gate_version': 2, 'passed': True,
        'model_requests': 0, 'code_tree_hash': 'current', 'tasks': [{'passed': True}, {'passed': True}]}))
    cli.require_source_precheck(tmp_path, manifest)


async def test_observed_discovery_preserves_shared_service_question(frozen, tmp_path, monkeypatch):
    import httpx

    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.segments import SegmentActivities
    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from research_domain.models import SourceCandidate, SourceStrategy
    from resources.model_client import ModelClient
    from web_retrieval.models import SearchRequest
    from web_retrieval.service import WebRetrievalService

    m, ledger = frozen
    m = {**m, 'profile': 'research-html-mvp'}
    received = []

    async def discover(self, query, *, strategy, limit, original_question=None):
        received.append(original_question)
        return [SourceCandidate('https://developer.mozilla.org/en-US/docs/Web/HTTP/Caching')]

    for target, name in [(httpx.AsyncClient, 'post'), (ModelClient, 'send_prepared'),
                         (ModelClient, 'before_dispatch'), (CapabilityActivities, 'close'),
                         (StaticWebContentProvider, 'fetch'), (SegmentActivities, 'release')]:
        monkeypatch.setattr(target, name, getattr(target, name))
    monkeypatch.setattr(CompositeSourceDiscoveryProvider, 'discover', discover)
    (tmp_path / 'active-sample').write_text('retrieval')
    worker.install_hooks(m, ledger)
    # A local fetch placeholder is sufficient: this test only exercises search dispatch.
    service = WebRetrievalService({'deepseek': CompositeSourceDiscoveryProvider([])}, {'local': object()},
                                  default_search='deepseek')
    result = await service.search(SearchRequest('HTTP cache', SourceStrategy(), original_question='What is no-cache?'))
    assert result.status == 'ok' and len(result.candidates) == 1
    assert received == ['What is no-cache?']
    assert ledger.summary()['requests'] == 0
