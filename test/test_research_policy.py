"""Research resumes admitted business policy and exact implementation contracts."""
from dataclasses import replace

import pytest

from research_domain.capability import research_definition


def test_new_policy_is_frozen_but_legacy_definition_serialization_is_unchanged():
    from research_domain.policy import ResearchPolicy
    old = research_definition().to_dict()
    assert old['implementation_version'] == 'research-web-v2'
    assert 'execution_policy' not in old
    policy = ResearchPolicy(synthesis_prompt='frozen research instruction', max_sources=7)
    new = research_definition(policy).to_dict()
    assert new['implementation_version'] == 'research-web-v3'
    assert new['execution_policy']['max_sources'] == 7
    assert new['execution_policy']['synthesis_prompt'] == 'frozen research instruction'
    assert new['limits'] == {'schema_version': 1, 'max_duration_seconds': 3600, 'max_model_calls': 1, 'max_tool_calls': 23}
    assert ResearchPolicy.from_snapshot(new['execution_policy']) == policy


def test_research_compatibility_accepts_supported_old_or_frozen_policy_not_arbitrary_definitions():
    from research_domain.capability import compatible_research_definition
    from research_domain.policy import ResearchPolicy
    first = research_definition(ResearchPolicy(synthesis_prompt='original', max_sources=7)).to_dict()
    current = research_definition(ResearchPolicy(synthesis_prompt='changed', max_sources=9)).to_dict()
    assert compatible_research_definition(first, current)
    assert compatible_research_definition(research_definition().to_dict(), current)
    for changed in ({**first, 'implementation_version': 'unknown'},
                    {**first, 'limits': {**first['limits'], 'max_model_calls': 99}},
                    {**first, 'execution_policy': {**first['execution_policy'], 'synthesis_prompt': 'tampered'}},
                    {**first, 'execution_policy': {**first['execution_policy'], 'unrecognized': 1}}):
        assert not compatible_research_definition(changed, current)


@pytest.mark.parametrize('changes', [{'max_sources': True}, {'min_evidence': -1},
    {'search_timeout_seconds': float('nan')}, {'search_provider': 'auto'},
    {'fetch_provider': 'browser'}, {'graph_version': 'new-graph'}, {'max_tokens': 999}])
def test_research_policy_rejects_unsupported_or_invalid_fields(changes):
    from research_domain.policy import ResearchPolicy
    with pytest.raises(ValueError, match='research_policy'):
        replace(ResearchPolicy(synthesis_prompt='instruction'), **changes)


@pytest.mark.asyncio
async def test_synthesis_request_uses_frozen_prompt_and_limits():
    from research_adapters.synthesis import ResourcePoolResearchSynthesizer
    from research_domain.policy import ResearchPolicy
    class Pool:
        async def generate(self, **kwargs):
            assert kwargs['messages'][0] == {'role': 'system', 'content': 'old instruction'}
            assert kwargs['messages'][1]['role'] == 'system'
            assert kwargs['messages'][1]['content'].startswith('当前时间：')
            assert kwargs['messages'][1]['content'].endswith('（Asia/Shanghai）')
            assert kwargs['max_tokens'] == 4096
            assert kwargs['model_selector'] == 'chat'
            return type('Response', (), {'content': '{}'})()
    await ResourcePoolResearchSynthesizer(Pool()).request('objective', [],
        policy=ResearchPolicy(synthesis_prompt='old instruction'))


@pytest.mark.asyncio
async def test_adapter_uses_frozen_search_and_inner_http_timeout_despite_current_defaults():
    import httpx

    from research_adapters.web import SearXNGDiscoveryProvider
    from research_domain.context import research_policy
    from research_domain.models import SourceStrategy
    from research_domain.policy import ResearchPolicy
    from web_retrieval.adapters import ResearchRetrievalAdapter
    from web_retrieval.service import WebRetrievalService
    seen = []
    def respond(request):
        seen.append((str(request.url), request.extensions['timeout']['read']))
        return httpx.Response(200, json={'results': []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), timeout=0.25) as client:
        provider = SearXNGDiscoveryProvider('https://fixture.example', client=client, timeout_seconds=0.25)
        service = WebRetrievalService({'legacy': provider, 'deepseek': provider}, {'local': object()}, default_search='deepseek')
        adapter = ResearchRetrievalAdapter(service, search_timeout=0.25)
        token = research_policy.set(ResearchPolicy(synthesis_prompt='frozen', search_timeout_seconds=17))
        try:
            result = await adapter.discover('a bounded query', strategy=SourceStrategy(), limit=1)
        finally:
            research_policy.reset(token)
    assert result.diagnostics['provider'] == 'legacy'
    assert seen[0][1] == 17


@pytest.mark.asyncio
async def test_missing_frozen_provider_never_switches_to_current_default():
    from research_domain.context import research_policy
    from research_domain.models import SourceStrategy
    from research_domain.policy import ResearchPolicy
    from web_retrieval.adapters import ResearchRetrievalAdapter
    from web_retrieval.service import WebRetrievalService
    token = research_policy.set(ResearchPolicy(synthesis_prompt='frozen', search_provider='deepseek'))
    try:
        adapter = ResearchRetrievalAdapter(WebRetrievalService({'legacy': object()}, {'local': object()}))
        with pytest.raises(ValueError, match='unavailable'):
            await adapter.discover('query', strategy=SourceStrategy(), limit=1)
    finally:
        research_policy.reset(token)


def test_legacy_research_descriptor_keeps_prechange_contract_and_digest():
    import json
    from pathlib import Path

    from run_domain.results import result_digest
    original = json.loads((Path(__file__).parent / 'fixtures/research/definition-web-v2.json').read_text())
    assert research_definition().to_dict() == original
    assert result_digest(research_definition().to_dict()) == result_digest(original)


@pytest.mark.asyncio
async def test_frozen_deepseek_fetch_keeps_current_final_url_denial():
    from research_domain.context import research_policy
    from research_domain.models import SourceCandidate
    from research_domain.policy import ResearchPolicy
    from web_retrieval.adapters import ResearchRetrievalAdapter
    from web_retrieval.service import WebRetrievalService
    token = research_policy.set(ResearchPolicy(synthesis_prompt='frozen', fetch_provider='deepseek'))
    try:
        adapter = ResearchRetrievalAdapter(WebRetrievalService({'legacy': object()}, {'deepseek': object()}, default_fetch='deepseek'))
        with pytest.raises(ValueError, match='final_url_unverifiable'):
            await adapter.fetch(SourceCandidate('https://fixture.example/'))
    finally:
        research_policy.reset(token)
