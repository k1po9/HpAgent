import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from actions.contracts import ActionRequest
from agent_activities.web_tools import NAMES, PROFILES, WebTools, discover
from research_adapters.deepseek import DeepSeekWebProvider
from research_adapters.discovery import CompositeSourceDiscoveryProvider
from research_adapters.web import DiscoveryCandidates
from research_domain.models import SourceCandidate
from resources.run_budget import RunBudgetExhausted
from run_domain.execution import StaleFencingToken
from run_domain.execution_context import fence_scope
from web_retrieval.budget import paid_before_request
from web_retrieval.safety import SafeLocalFetcher
from web_retrieval.service import WebRetrievalService


class Budget:
    def __init__(self):
        self.keys, self.calls = set(), []
    def reserve(self, run, key, amounts, *, enforce_limits=False):
        assert enforce_limits
        replayed = key in self.keys
        self.keys.add(key)
        self.calls.append(('reserve', key))
        return SimpleNamespace(replayed=replayed)
    def settle(self, run, key, amounts, source):
        self.calls.append(('settle', key))


class Search:
    def __init__(self):
        self.calls = []
    async def discover(self, query, **kwargs):
        self.calls.append(kwargs)
        return DiscoveryCandidates([SourceCandidate('https://docs.python.org/a', 'Tasks', 'TaskGroup')])


@pytest.fixture
def request_identity():
    request = SimpleNamespace(account_id=str(uuid4()), run_id=str(uuid4()),
        execution_id=str(uuid4()), lease_token=7, operation_id='main:web:stable')
    with fence_scope(request.account_id, request.run_id, request.lease_token, request.execution_id):
        yield request


def test_manifests_require_no_model_identity_or_research_child():
    assert {t['function']['name'] for t in discover()} == NAMES
    for tool in discover():
        schema = tool['function']['parameters']
        assert schema['additionalProperties'] is False
        assert not {'account_id', 'run_id', 'execution_id', 'fence'} & schema['properties'].keys()
        assert 'deep' not in schema['properties']['mode']['enum']


@pytest.mark.parametrize('mode,count', [('quick', 3), ('standard', 8)])
async def test_main_search_uses_size_profiles_and_one_local_charge(request_identity, mode, count):
    search, budget = Search(), Budget()
    tools = WebTools(WebRetrievalService({'legacy': search}, {'local': object()}), budget)
    result = await tools.execute(ActionRequest('id', 'web_search', {'query': 'TaskGroup', 'mode': mode}), request_identity)
    assert result.success and result.output['sources'][0]['url'] == 'https://docs.python.org/a'
    assert search.calls[0]['limit'] == count
    assert not search.calls[0]['strategy'].github and not search.calls[0]['strategy'].rss
    assert len(budget.keys) == 1 and len(budget.calls) == 2
    with pytest.raises(ApplicationError, match='reconciliation') as error:
        await tools.execute(ActionRequest('id', 'web_search', {'query': 'TaskGroup'}), request_identity)
    assert error.value.non_retryable and len(search.calls) == 1


@pytest.mark.parametrize('arguments', [
    {'query': 'query', 'run_id': 'forged'}, {'query': 'query', 'mode': 'deep'},
    {'query': 'x' * 129}, {'query': 'query', 'allowed_domains': ['https://docs.python.org']},
    {'query': '   '}, {'query': 'query', 'provider': 'deepseek'},
])
async def test_bad_or_unavailable_requests_do_not_charge(request_identity, arguments):
    search, budget = Search(), Budget()
    tools = WebTools(WebRetrievalService({'legacy': search}, {'local': object()}), budget)
    result = await tools.execute(ActionRequest('id', 'web_search', arguments), request_identity)
    assert not result.success and not budget.calls and not search.calls


async def test_main_cloud_uses_same_paid_gate_once_and_returns_explicit_unknown_final(request_identity):
    budget, sent = Budget(), []
    def handle(request):
        assert budget.calls[-1][0] == 'settle'
        sent.append(request)
        return httpx.Response(200, json={'title': 'Tasks', 'content': '# TaskGroup\n\nExact Markdown.'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        cloud = DeepSeekWebProvider('test-only', client=client, before_request=paid_before_request)
        service = WebRetrievalService({'legacy': Search()}, {'local': object(), 'deepseek': cloud})
        result = await WebTools(service, budget).execute(ActionRequest('id', 'web_fetch',
            {'url': 'https://docs.python.org/a', 'provider': 'deepseek'}), request_identity)
    assert result.success and result.output['final_url'] is None
    assert result.output['final_url_verified'] is False and result.output['format'] == 'markdown'
    assert len(budget.keys) == len(sent) == 1
    assert all('paid:fetch' in key for key in budget.keys)


async def test_cloud_network_unknown_is_a_control_failure_and_never_resends(request_identity):
    budget, sent = Budget(), []
    def handle(request):
        sent.append(request)
        raise httpx.ReadTimeout('private upstream text')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        cloud = DeepSeekWebProvider('test-only', client=client, before_request=paid_before_request)
        service = WebRetrievalService({'deepseek': CompositeSourceDiscoveryProvider([cloud])},
            {'local': object()}, default_search='deepseek')
        for _ in range(2):
            with pytest.raises(ApplicationError) as error:
                await WebTools(service, budget).execute(ActionRequest('id', 'web_search', {'query': 'query'}), request_identity)
            assert error.value.type == 'retrieval_usage_unknown' and error.value.non_retryable
            assert 'private upstream' not in str(error.value)
    assert len(sent) == 1


async def test_budget_exhaustion_precedes_dispatch(request_identity):
    class Exhausted(Budget):
        def reserve(self, *args, **kwargs):
            raise RunBudgetExhausted('tool_calls')
    search = Search()
    with pytest.raises(ApplicationError) as error:
        await WebTools(WebRetrievalService({'legacy': search}, {'local': object()}), Exhausted()).execute(
            ActionRequest('id', 'web_search', {'query': 'query'}), request_identity)
    assert error.value.type == 'run_budget_exhausted' and not search.calls


async def test_missing_or_changed_fence_cannot_use_injected_identity(request_identity):
    budget = Budget()
    tools = WebTools(WebRetrievalService({'legacy': Search()}, {'local': object()}), budget)
    with fence_scope(str(uuid4()), request_identity.run_id, 7, request_identity.execution_id):
        with pytest.raises(StaleFencingToken):
            await tools.execute(ActionRequest('id', 'web_search', {'query': 'query'}), request_identity)
    assert not budget.calls


@pytest.mark.parametrize('mode', ['quick', 'standard'])
async def test_local_fetch_bounds_unicode_json_and_exposes_truncation(request_identity, mode):
    async def resolver(host, port):
        return '93.184.216.34'
    text = '中文\\\n' * 10000
    local = SafeLocalFetcher(resolver=resolver, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=text)), extractor=lambda html, _: html)
    service = WebRetrievalService({'legacy': Search()}, {'local': local})
    result = await WebTools(service, Budget()).execute(ActionRequest('id', 'web_fetch',
        {'url': 'https://docs.python.org/a', 'mode': mode}), request_identity)
    assert result.success and result.output['truncated'] is True
    assert result.output['final_url_verified'] is True
    assert text.startswith(result.output['text'])
    assert len(json.dumps(result.output, ensure_ascii=False, separators=(',', ':')).encode()) <= PROFILES[mode][3]
