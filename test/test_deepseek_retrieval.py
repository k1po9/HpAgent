import json

import httpx
import pytest

from research_adapters.deepseek import DeepSeekError, DeepSeekWebProvider, public_web_url
from research_domain.models import SourceCandidate, SourceStrategy, content_sha256


@pytest.mark.asyncio
async def test_protocol_and_exact_markdown():
    calls = []
    markdown = '# TaskGroup\n\nExact  whitespace.\n'

    def handle(request):
        calls.append(json.loads(request.content))
        assert request.headers['Authorization'] == 'Bearer test-only'
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json={'results': [{'url': 'https://docs.python.org/a',
                'title': 'Tasks', 'snippet': 'TaskGroup', 'summary': 'long summary',
                'date': None, 'rerank_score': 2.5, 'authority': 3}]})
        return httpx.Response(200, json={'title': 'Tasks', 'content': markdown})

    attempts = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = DeepSeekWebProvider('test-only', client=client, before_request=attempts.append)
        result = await provider.discover('asyncio gather', strategy=SourceStrategy(), limit=5)
        content = await provider.fetch(result[0])
    assert calls == [{'query': 'asyncio gather', 'count': 5, 'need_content': False},
                     {'url': 'https://docs.python.org/a'}]
    assert attempts == ['search', 'fetch']
    assert content.text == markdown
    assert content.content_hash == content_sha256(markdown)
    assert content.metadata['final_url'] is None
    assert result[0].metadata['summary'] == 'long summary'


@pytest.mark.asyncio
async def test_limits_before_network_and_no_retry():
    attempts = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(402, text='sensitive upstream details'))) as client:
        provider = DeepSeekWebProvider('test-only', client=client, before_request=attempts.append)
        with pytest.raises(ValueError):
            await provider.discover('长' * 129, strategy=SourceStrategy(), limit=5)
        assert attempts == []
        with pytest.raises(DeepSeekError, match='http_402') as error:
            await provider.fetch(SourceCandidate('https://docs.python.org/a'))
        assert str(error.value) == 'http_402'
        assert attempts == ['fetch']


@pytest.mark.parametrize('url', ['file:///tmp/a', 'http://127.0.0.1',
    'http://[::1]', 'https://user:password@example.com', 'http://metadata.internal/a',
    'http://169.254.169.254', 'http://localhost', 'https://example.com:9000'])
def test_reject_unsafe_url(url):
    with pytest.raises(ValueError):
        public_web_url(url)


@pytest.mark.asyncio
async def test_response_bound_and_empty_search():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={'results': []}))) as client:
        provider = DeepSeekWebProvider('test-only', client=client)
        result = await provider.discover('query', strategy=SourceStrategy(), limit=5)
        assert not result and result.diagnostics['status'] == 'empty'
        provider.max_bytes = 1
        with pytest.raises(DeepSeekError, match='response_too_large'):
            await provider.discover('query', strategy=SourceStrategy(), limit=5)


def test_experiment_cap_persists_and_corruption_fails_closed(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / 'scripts/experiments/web_retrieval_compare.py'
    spec = importlib.util.spec_from_file_location('retrieval_compare', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger = tmp_path / 'attempts.jsonl'
    monkeypatch.setattr(module, 'LEDGER', ledger)
    ledger.write_text(''.join(json.dumps({'attempt': index, 'operation': 'search',
        'at': '2026-10-23T00:00:00+00:00'}) + '\n' for index in range(1, 1000)))
    module.reserve_attempt('fetch')
    with pytest.raises(RuntimeError, match='experiment_call_limit'):
        module.reserve_attempt('search')
    assert len(ledger.read_text().splitlines()) == 1000
    ledger.write_text('broken')
    with pytest.raises(RuntimeError, match='invalid_call_log'):
        module.reserve_attempt('search')


@pytest.mark.parametrize('history', [None, '', '{}\n', 'null\n',
    '{"attempt":2,"operation":"search","at":"2026-10-23T00:00:00+00:00"}\n',
    '{"attempt":1,"operation":"search","at":"2026-10-23T00:00:00"}\n',
    '{"attempt":1,"operation":"fetch","at":"2026-10-23T00:00:00+00:00"}'])
def test_experiment_missing_or_damaged_history_never_restarts(tmp_path, monkeypatch, history):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / 'scripts/experiments/web_retrieval_compare.py'
    spec = importlib.util.spec_from_file_location('retrieval_compare_history', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger = tmp_path / 'attempts.jsonl'
    monkeypatch.setattr(module, 'LEDGER', ledger)
    if history is not None:
        ledger.write_text(history)
    with pytest.raises(RuntimeError, match='missing_call_log|invalid_call_log'):
        module.reserve_attempt('search')
    assert (ledger.read_text() if ledger.exists() else None) == history


@pytest.mark.asyncio
async def test_comparison_local_download_bound():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / 'scripts/experiments/web_retrieval_compare.py'
    spec = importlib.util.spec_from_file_location('retrieval_compare_bound', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b'x' * 2_000_001))) as client:
        provider = module.BoundedComparisonFetcher(module.W3libSourceCanonicalizer(), client=client)
        with pytest.raises(ValueError, match='response_too_large'):
            await provider.fetch(SourceCandidate('https://docs.python.org/a'))
