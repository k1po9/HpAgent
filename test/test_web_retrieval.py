from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from orchestration.config import ResearchConfig
from research_adapters.deepseek import DeepSeekWebProvider
from research_adapters.discovery import CompositeSourceDiscoveryProvider
from research_adapters.web import DiscoveryCandidates, discovery_query
from research_domain.excerpts import named_concepts
from research_domain.models import SourceCandidate, SourceStrategy, content_sha256
from resources.run_budget import RunBudgetExhausted
from web_retrieval.adapters import ResearchRetrievalAdapter
from web_retrieval.budget import paid_before_request, retrieval_budget_scope
from web_retrieval.composition import compose_retrieval
from web_retrieval.models import FetchRequest, SearchRequest
from web_retrieval.safety import RetrievalDenied, SafeLocalFetcher, resolve_public
from web_retrieval.service import WebRetrievalService


class Discovery:
    def __init__(self, candidates):
        self.candidates, self.calls = candidates, 0

    async def discover(self, query, **kwargs):
        self.calls += 1
        return DiscoveryCandidates(self.candidates)


def service(discovery=None, fetch=None, **kwargs):
    return WebRetrievalService({'legacy': discovery or Discovery([])},
                               {'local': fetch or object()}, **kwargs)


def test_scope_roundtrip_preserves_old_policy_identity():
    old = SourceStrategy().to_dict()
    assert 'allowed_domains' not in old
    strategy = SourceStrategy(allowed_domains=('DOCS.PYTHON.ORG.',),
                              excluded_domains=('private.docs.python.org',))
    assert SourceStrategy.from_dict(strategy.to_dict()) == strategy
    assert strategy.allowed_domains == ('docs.python.org',)
    with pytest.raises(ValueError):
        SourceStrategy(allowed_domains=('https://docs.python.org/a',))


async def test_hard_scope_preferences_dedup_and_index_filter():
    urls = ['https://other.org/a', 'https://docs.python.org/a',
            'https://docs.python.org/a#second', 'https://private.docs.python.org/a',
            'https://evilpython.org/a', 'https://docs.python.org/genindex.html',
            'https://docs.python.org/ru/3.13/library/asyncio-api-index.html']
    provider = Discovery([SourceCandidate(u) for u in urls])
    result = await service(provider).search(SearchRequest('asyncio', SourceStrategy(
        allowed_domains=('python.org',), excluded_domains=('private.docs.python.org',))))
    assert result.status == 'partial'
    assert [c.uri for c in result.candidates] == ['https://docs.python.org/a']
    assert result.diagnostics['rejected'] == {'source_scope_denied': 3, 'duplicate_url': 1,
                                            'navigation_index': 2}
    result = await service(provider).search(SearchRequest('asyncio', SourceStrategy(
        preferred_domains=('docs.python.org',))))
    assert any(c.uri == urls[0] for c in result.candidates)  # preference is not a hard filter


async def test_selection_never_falls_back_and_cloud_limits_precede_send():
    legacy, cloud = Discovery([]), Discovery([])
    svc = WebRetrievalService({'legacy': legacy, 'deepseek': cloud}, {'local': object()})
    assert (await svc.search(SearchRequest('query', provider='unknown'))).status == 'denied'
    assert (await svc.search(SearchRequest('x' * 129, provider='deepseek'))).status == 'denied'
    await svc.search(SearchRequest('query', provider='deepseek'))
    assert (legacy.calls, cloud.calls) == (0, 1)
    await svc.search(SearchRequest('query'))
    assert legacy.calls == 1


def test_query_preserves_gather_and_drops_schedule_metadata():
    objective = ('Compare asyncio TaskGroup gather Lock. Updated 2026-10-23 UTC. '
                 + 'Please retain complete documented conditions. ' * 20)
    query = discovery_query(objective, SourceStrategy(preferred_domains=('docs.python.org',)), max_chars=128)
    assert len(query) <= 128
    assert all(t in query.lower() for t in ('taskgroup', 'gather', 'lock'))
    assert 'utc' not in query.lower() and '2026' not in query
    assert 'utc' not in named_concepts(objective)


async def public_resolver(host, port):
    return '93.184.216.34'


def local(handler, **kwargs):
    return SafeLocalFetcher(resolver=public_resolver, transport=httpx.MockTransport(handler),
                            extractor=lambda html, uri: html, **kwargs)


async def test_local_fetch_pins_target_retains_host_sni_and_exact_hash():
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path == '/a':
            return httpx.Response(302, headers={'location': '/b'})
        return httpx.Response(200, text='Exact  source\n\nwords', headers={'content-type': 'text/html'})
    result = await service(fetch=local(handle)).fetch(FetchRequest('https://docs.python.org/a'))
    assert result.status == 'ok'
    assert result.final_url == 'https://docs.python.org/b'
    assert result.content.text == 'Exact  source\n\nwords'
    assert result.content.content_hash == content_sha256(result.content.text)
    assert all(c.url.host == '93.184.216.34' for c in calls)
    assert all(c.headers['host'] == 'docs.python.org' for c in calls)
    assert all(c.extensions['sni_hostname'] == 'docs.python.org' for c in calls)


@pytest.mark.parametrize('target', ['http://127.0.0.1/a', 'https://other.org/a',
                                   'https://excluded.python.org/a'])
async def test_redirect_scope_is_checked_before_next_connection(target):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': target})
    result = await service(fetch=local(handle)).fetch(FetchRequest('https://docs.python.org/a',
        SourceStrategy(allowed_domains=('python.org',), excluded_domains=('excluded.python.org',))))
    assert result.status == 'denied' and len(calls) == 1


async def test_dns_private_mixed_answers_are_denied(monkeypatch):
    import asyncio
    async def lookup(*args, **kwargs):
        return [(0, 0, 0, '', ('93.184.216.34', 443)), (0, 0, 0, '', ('127.0.0.1', 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', lookup)
    with pytest.raises(RetrievalDenied, match='non_public_target'):
        await resolve_public('docs.python.org', 443)


async def test_limits_browser_and_unknown_final_url():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, text='x' * 11)
    svc = service(fetch=local(handle))
    assert (await svc.fetch(FetchRequest('https://docs.python.org/a', max_bytes=10))).status == 'denied'
    assert (await svc.fetch(FetchRequest('https://docs.python.org/a', browser_permission=True))).status == 'denied'
    assert len(calls) == 1
    cloud = DeepSeekWebProvider('test-only', client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    svc.fetches['deepseek'] = cloud
    result = await svc.fetch(FetchRequest('https://docs.python.org/a', provider='deepseek', require_final_url=True))
    assert result.diagnostics['reason'] == 'final_url_unverifiable' and len(calls) == 1
    await cloud.client.aclose()


class Budget:
    def __init__(self):
        self.keys, self.charged = set(), []
    def reserve(self, run_id, key, values, **kwargs):
        replay = key in self.keys
        self.keys.add(key)
        return SimpleNamespace(replayed=replay)
    def settle(self, run_id, key, values, source):
        self.charged.append((key, values, source))


async def test_paid_attempt_is_charged_before_send_and_replay_never_resends():
    budget, sends = Budget(), []
    def handle(request):
        assert budget.charged
        sends.append(request)
        raise httpx.ReadTimeout('secret upstream details')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        cloud = DeepSeekWebProvider('test-only', client=client, before_request=paid_before_request)
        svc = WebRetrievalService({'deepseek': CompositeSourceDiscoveryProvider([cloud])},
                                  {'local': object()}, default_search='deepseek')
        assert (await svc.search(SearchRequest('query'))).status == 'denied'
        with retrieval_budget_scope(budget, uuid4(), 'stable-source-discovery'):
            first = await svc.search(SearchRequest('query'))
            second = await svc.search(SearchRequest('query'))
        assert first.status == second.status == 'failed'
        assert first.diagnostics['usage_unknown'] is True
        assert second.diagnostics['usage_unknown'] is True
        assert len(sends) == len(budget.charged) == 1


async def test_paid_budget_exhaustion_is_not_swallowed_by_composite():
    class Exhausted(Budget):
        def reserve(self, *args, **kwargs):
            raise RunBudgetExhausted('tool_calls')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('sent'))) as client:
        provider = DeepSeekWebProvider('test-only', client=client, before_request=paid_before_request)
        composite = CompositeSourceDiscoveryProvider([provider])
        with retrieval_budget_scope(Exhausted(), uuid4(), 'stable'):
            with pytest.raises(RunBudgetExhausted):
                await composite.discover('query', strategy=SourceStrategy(), limit=1)


async def test_auth_stop_prevents_later_requests_in_provider_instance():
    sends = []
    def handle(request):
        sends.append(request)
        return httpx.Response(402)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        cloud = DeepSeekWebProvider('test-only', client=client)
        for _ in range(2):
            with pytest.raises(Exception):
                await cloud.fetch(SourceCandidate('https://docs.python.org/a'))
    assert len(sends) == 1


def test_composition_defaults_and_explicit_unavailable_selection():
    adapter = compose_retrieval(ResearchConfig(), None)
    assert isinstance(adapter, ResearchRetrievalAdapter)
    assert adapter.service.default_search == 'legacy'
    assert adapter.service.default_fetch == 'local'
    with pytest.raises(ValueError, match='retrieval_default_unavailable'):
        compose_retrieval(replace(ResearchConfig(), search_provider='deepseek'), None)


async def test_cloud_markdown_keeps_exact_spans_and_unknown_final_url():
    from research_domain.excerpts import evidence_spans

    markdown = ('## TaskGroup\n\nTaskGroup cancels remaining tasks when one task fails.\n\n'
                '## asyncio.gather\\(\\)\n\ngather propagates the first exception by default; '
                'other awaitables continue running.\n\n'
                '## Lock\n\nLock acquisition is fair among waiting coroutines.\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={'title': 'Tasks', 'content': markdown}))) as client:
        cloud = DeepSeekWebProvider('test-only', client=client)
        svc = WebRetrievalService({'legacy': Discovery([])}, {'deepseek': cloud}, default_fetch='deepseek')
        result = await svc.fetch(FetchRequest('https://docs.python.org/a', format='markdown'))
    assert result.status == 'ok' and result.final_url is None
    assert result.content.text == markdown
    spans = evidence_spans(result.content.text, 'Compare TaskGroup gather Lock')
    selected = '\n'.join(result.content.text[a:b] for a, b, _ in spans)
    assert all(term in selected for term in ('TaskGroup', 'gather', 'Lock'))
    assert result.content.content_hash == content_sha256(markdown)


async def test_original_question_reaches_only_deepseek_protocol():
    payloads = []
    def handle(request):
        import json
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'results': []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        cloud = DeepSeekWebProvider('test-only', client=client)
        svc = WebRetrievalService({'deepseek': CompositeSourceDiscoveryProvider([cloud])},
                                  {'local': object()}, default_search='deepseek')
        result = await svc.search(SearchRequest('query', original_question='Original question'))
    assert result.status == 'empty'
    assert payloads == [{'query': 'query', 'count': 10, 'need_content': False,
                         'ori_question': 'Original question'}]


async def test_streamed_gzip_is_decoded_once_for_local_and_legacy_search():
    import gzip
    import json

    from research_adapters.web import SearXNGDiscoveryProvider

    body = gzip.compress(b'Exact extracted source')
    result = await service(fetch=local(lambda _: httpx.Response(200, content=body,
        headers={'content-encoding': 'gzip', 'content-type': 'text/html'}))).fetch(
            FetchRequest('https://docs.python.org/a'))
    assert result.status == 'ok' and result.content.text == 'Exact extracted source'
    payload = gzip.compress(json.dumps({'results': [{'url': 'https://docs.python.org/a'}]}).encode())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
            content=payload, headers={'content-encoding': 'gzip'}))) as client:
        search = SearXNGDiscoveryProvider('http://trusted-search', client=client)
        candidates = await search.discover('query', strategy=SourceStrategy(), limit=1)
    assert len(candidates) == 1


async def test_cloud_text_conversion_and_hard_final_scope_are_explicitly_unsupported():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('sent'))) as client:
        cloud = DeepSeekWebProvider('test-only', client=client)
        svc = WebRetrievalService({'legacy': Discovery([])}, {'deepseek': cloud}, default_fetch='deepseek')
        text = await svc.fetch(FetchRequest('https://docs.python.org/a', format='text'))
        assert text.status == 'denied' and text.diagnostics['reason'] == 'unsupported_format'
        scope = await svc.fetch(FetchRequest('https://docs.python.org/a', format='markdown',
            strategy=SourceStrategy(allowed_domains=('python.org',))))
        assert scope.status == 'denied' and scope.diagnostics['reason'] == 'final_url_unverifiable'
