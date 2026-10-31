from __future__ import annotations

import sys
from types import SimpleNamespace

import httpx
import pytest

from orchestration.config import AppConfig
from research_adapters.discovery import (
    CompositeSourceDiscoveryProvider,
    GitHubDiscoveryProvider,
    RSSDiscoveryProvider,
)
from research_adapters.synthesis import ResourcePoolResearchSynthesizer
from research_adapters.web import (
    PlaywrightBrowserFetchProvider,
    SearXNGDiscoveryProvider,
    StaticWebContentProvider,
)
from research_domain.models import ClaimDraft, SourceCandidate, SourceStrategy, content_sha256


def test_source_strategy_and_sha256_are_domain_stable():
    strategy = SourceStrategy.from_dict({"freshness_days": 3, "preferred_domains": ["example.com"]})
    assert strategy.to_dict()["preferred_domains"] == ["example.com"]
    assert content_sha256("alpha\n beta") == content_sha256("alpha beta")
    with pytest.raises(ValueError):
        SourceStrategy(freshness_days=-1)
    with pytest.raises(ValueError, match="at least one EvidenceItem"):
        ClaimDraft("unsupported", ())


def test_research_config_preserves_hindsight_missions():
    config = AppConfig()
    from pathlib import Path

    from orchestration.config import PromptsConfig
    prompts = PromptsConfig.from_dir(Path(__file__).resolve().parents[1] / "config/prompts")
    assert prompts.memory["retain_mission"]
    assert prompts.memory["observations_mission"]
    assert prompts.memory["recall_instruction"]
    assert not hasattr(config.hindsight, "reflect_mission")
    assert not hasattr(config.research, "retain_mission")


@pytest.mark.asyncio
async def test_githubkit_results_are_translated_without_leaking_tool_payloads():
    item = SimpleNamespace(
        model_dump=lambda: {
            "html_url": "https://github.com/example/project",
            "full_name": "example/project",
            "description": "Project",
            "stargazers_count": 7,
            "language": "Python",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )

    class Search:
        async def async_repos(self, **kwargs):
            assert kwargs == {"q": "topic", "per_page": 5}
            return SimpleNamespace(parsed_data=SimpleNamespace(items=[item]))

    client = SimpleNamespace(rest=SimpleNamespace(search=Search()))
    result = await GitHubDiscoveryProvider(client).discover(
        "topic", strategy=SourceStrategy(), limit=5
    )
    assert [(item.provider, item.source_type) for item in result] == [
        ("githubkit", "github_repository")
    ]
    assert result[0].metadata["preferred_domain"] is False


@pytest.mark.asyncio
async def test_rss_uses_httpx_and_feedparser_and_respects_configured_feeds(monkeypatch):
    parsed = SimpleNamespace(
        feed={"title": "Updates"},
        entries=[{"link": "https://example.com/a", "title": "Topic update", "summary": "news"}],
    )
    monkeypatch.setitem(sys.modules, "feedparser", SimpleNamespace(parse=lambda _body: parsed))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"rss", request=request)
    )
    client = httpx.AsyncClient(transport=transport)
    try:
        result = await RSSDiscoveryProvider(client=client).discover(
            "Topic",
            strategy=SourceStrategy(
                rss_feeds=("https://example.com/feed",),
                preferred_domains=("example.com",),
            ),
            limit=3,
        )
    finally:
        await client.aclose()
    assert result[0].provider == "feedparser"
    assert result[0].metadata["feed_uri"] == "https://example.com/feed"
    assert result[0].metadata["preferred_domain"] is True


@pytest.mark.asyncio
async def test_composite_discovery_deduplicates_provider_results():
    class Provider:
        async def discover(self, query, *, strategy, limit):
            return [SourceCandidate("https://example.com/a")]

    result = await CompositeSourceDiscoveryProvider([Provider(), Provider()]).discover(
        "topic", strategy=SourceStrategy(), limit=5
    )
    assert [item.uri for item in result] == ["https://example.com/a"]


@pytest.mark.asyncio
async def test_composite_discovery_round_robins_providers_before_filling_limit():
    class Provider:
        def __init__(self, name):
            self.name = name

        async def discover(self, query, *, strategy, limit):
            return [
                SourceCandidate(f"https://{self.name}.example/{index}", provider=self.name)
                for index in range(limit)
            ]

    result = await CompositeSourceDiscoveryProvider(
        [Provider("search"), Provider("github"), Provider("rss")]
    ).discover("topic", strategy=SourceStrategy(), limit=4)
    assert [item.provider for item in result] == ["search", "github", "rss", "search"]


@pytest.mark.asyncio
async def test_composite_discovery_isolates_one_provider_failure(caplog):
    class FailedProvider:
        async def discover(self, query, *, strategy, limit):
            raise TimeoutError("provider timed out")

    class HealthyProvider:
        async def discover(self, query, *, strategy, limit):
            return [SourceCandidate("https://healthy.example/a", provider="healthy")]

    result = await CompositeSourceDiscoveryProvider(
        [FailedProvider(), HealthyProvider()]
    ).discover("topic", strategy=SourceStrategy(), limit=5)
    assert [item.provider for item in result] == ["healthy"]
    assert "source discovery provider failed" in caplog.text


@pytest.mark.asyncio
async def test_composite_discovery_fails_when_every_provider_fails():
    class FailedProvider:
        async def discover(self, query, *, strategy, limit):
            raise TimeoutError

    results = await CompositeSourceDiscoveryProvider([FailedProvider(), FailedProvider()]).discover(
        "topic", strategy=SourceStrategy(), limit=5
    )
    assert results == []
    assert results.diagnostics["status"] == "all_unavailable"


@pytest.mark.asyncio
async def test_synthesizer_rejects_unknown_evidence_references():
    class Pool:
        async def generate(self, **kwargs):
            return SimpleNamespace(
                content='{"title":"R","report_markdown":"# R",'
                '"claims":[{"statement":"x","evidence_ids":["missing"]}]}'
            )

    with pytest.raises(ValueError, match="unknown EvidenceItem"):
        await ResourcePoolResearchSynthesizer(Pool()).synthesize(
            "objective", [{"evidence_id": "known"}]
        )


@pytest.mark.asyncio
async def test_synthesizer_extracts_complete_json_object_from_model_wrapping():
    class Pool:
        async def generate(self, **kwargs):
            assert kwargs["model_selector"] == "chat"
            return SimpleNamespace(
                content="Result follows:\n```json\n"
                '{"title":"R","report_markdown":"# R\\n\\nFact [E1].",'
                '"claims":[{"statement":"Fact","importance":"important",'
                '"evidence_ids":["E1"]}]}\n```\ntrailing text'
            )

    report = await ResourcePoolResearchSynthesizer(Pool()).synthesize(
        "objective", [{"evidence_id": "E1"}]
    )
    assert report.title == "R"
    assert report.claims[0].evidence_ids == ("E1",)


def test_synthesizer_rejects_truncated_json():
    with pytest.raises(ValueError, match="complete JSON object"):
        ResourcePoolResearchSynthesizer._decode_object('{"title":"truncated"')


@pytest.mark.asyncio
async def test_searxng_transport_is_translated_to_source_candidates():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/a?utm_source=x",
                        "title": "A",
                        "content": "snippet",
                        "engine": "brave",
                        "score": 1.5,
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = SearXNGDiscoveryProvider("http://searxng:8080", client=client)
        result = await provider.discover(
            "topic", strategy=SourceStrategy(preferred_domains=("example.com",)), limit=10
        )
    finally:
        await client.aclose()
    assert len(result) == 1
    assert result[0].provider == "searxng"
    assert result[0].metadata == {
        "engine": "brave",
        "engines": [],
        "score": 1.5,
        "preferred_domain": True,
    }


@pytest.mark.parametrize(
    ("https_proxy", "http_proxy", "no_proxy", "expected"),
    [
        (None, None, None, {"headless": True}),
        (
            "http://secure-proxy:7890",
            "http://fallback-proxy:8080",
            None,
            {"headless": True, "proxy": {"server": "http://secure-proxy:7890"}},
        ),
        (
            None,
            "http://fallback-proxy:8080",
            None,
            {"headless": True, "proxy": {"server": "http://fallback-proxy:8080"}},
        ),
        (
            "http://secure-proxy:7890",
            None,
            " localhost, 127.0.0.1, searxng ",
            {
                "headless": True,
                "proxy": {
                    "server": "http://secure-proxy:7890",
                    "bypass": "localhost,127.0.0.1,searxng",
                },
            },
        ),
    ],
)
def test_playwright_launch_options_follow_runtime_proxy_environment(
    monkeypatch, https_proxy, http_proxy, no_proxy, expected
):
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    if https_proxy:
        monkeypatch.setenv("HTTPS_PROXY", https_proxy)
    if http_proxy:
        monkeypatch.setenv("HTTP_PROXY", http_proxy)
    if no_proxy:
        monkeypatch.setenv("NO_PROXY", no_proxy)
    assert PlaywrightBrowserFetchProvider._launch_options() == expected


@pytest.mark.asyncio
async def test_static_fetch_does_not_launch_browser_when_content_is_sufficient():
    browser_calls: list[str] = []

    class Browser:
        async def fetch_html(self, uri: str) -> str:
            browser_calls.append(uri)
            return "rendered"

    class Canonicalizer:
        def canonicalize(self, uri: str) -> str:
            return uri.split("#", 1)[0]

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text="static",
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )
    )
    client = httpx.AsyncClient(transport=transport)
    try:
        provider = StaticWebContentProvider(
            Canonicalizer(),
            browser=Browser(),
            client=client,
            min_content_chars=5,
            extractor=lambda html, _uri: "enough text" if html == "static" else html,
        )
        content = await provider.fetch(SourceCandidate("https://example.com/page", title="T"))
    finally:
        await client.aclose()
    assert content.text == "enough text"
    assert content.metadata["fetch_mode"] == "httpx"
    assert browser_calls == []


@pytest.mark.asyncio
async def test_playwright_fallback_is_used_only_after_insufficient_static_text():
    class Browser:
        calls = 0

        async def fetch_html(self, uri: str) -> str:
            self.calls += 1
            return "rendered html"

    class Canonicalizer:
        def canonicalize(self, uri: str) -> str:
            return uri

    browser = Browser()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="shell", request=request)
    )
    client = httpx.AsyncClient(transport=transport)
    try:
        provider = StaticWebContentProvider(
            Canonicalizer(),
            browser=browser,
            client=client,
            min_content_chars=20,
            extractor=lambda html, _uri: "x" if html == "shell" else "rendered body text long",
        )
        content = await provider.fetch(SourceCandidate("https://example.com/dynamic"))
    finally:
        await client.aclose()
    assert browser.calls == 1
    assert content.metadata["fetch_mode"] == "playwright"
    assert content.text == "rendered body text long"


@pytest.mark.asyncio
async def test_static_timeout_retries_before_browser_fallback():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timeout", request=request)

    class Browser:
        calls = 0

        async def fetch_html(self, uri: str) -> str:
            self.calls += 1
            return "rendered"

    class Canonicalizer:
        def canonicalize(self, uri: str) -> str:
            return uri

    browser = Browser()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = StaticWebContentProvider(
            Canonicalizer(),
            browser=browser,
            client=client,
            min_content_chars=5,
            max_attempts=2,
            extractor=lambda html, _uri: "browser body" if html == "rendered" else "",
        )
        content = await provider.fetch(SourceCandidate("https://example.com/timeout"))
    finally:
        await client.aclose()
    assert attempts == 2
    assert browser.calls == 1
    assert content.metadata["fetch_mode"] == "playwright"


@pytest.mark.parametrize(('content', 'code'), [
    ('{"title":', 'invalid_json'),
    ('{"title":"R","report_markdown":"unsupported","claims":{}}', 'invalid_fields'),
    ('{"title":"R","report_markdown":"unsupported","claims":[]}', 'missing_claims'),
    ('{"title":"R","report_markdown":"unsupported","claims":[{"statement":"fact","evidence_ids":[]}]}', 'missing_citation'),
    ('{"title":"R","report_markdown":"unsupported","claims":[{"statement":"fact","evidence_ids":["absent"]}]}', 'unknown_evidence'),
    ('{"title":"R","report_markdown":"unsupported","claims":[{"statement":"fact","evidence_ids":"known"}]}', 'invalid_fields'),
])
def test_synthesis_validation_codes_preserve_failure_reason(content, code):
    from research_adapters.synthesis import SynthesisValidationError
    with pytest.raises(SynthesisValidationError) as error:
        ResourcePoolResearchSynthesizer.parse({'content': content}, 'objective', [{'evidence_id': 'known'}])
    assert error.value.code == code


def test_semantic_main_keeps_definition_names_and_excludes_navigation():
    html = '''<html><body><nav>ExcludedNavigation</nav><main>
    <h1>Primitives</h1><dl><dt><code>example.</code><strong>Mailbox</strong></dt>
    <dd><p>Delivers messages in order.</p></dd></dl>
    <aside>ExcludedSidebar</aside><script>ExcludedScript</script>
    </main></body></html>'''
    text = StaticWebContentProvider._extract(html, 'https://docs.example/primitives')
    assert 'example.Mailbox' in text
    assert 'Delivers messages in order.' in text
    assert 'Excluded' not in text


def test_synthesis_deliverable_contains_only_registered_claims_and_references():
    import json

    response = {"content": json.dumps({"title": "R", "report_markdown": "Supported fact. Also invent an unsupported API rule.",
        "claims": [{"statement": "Supported fact", "evidence_ids": ["E1"]}]})}
    report = ResourcePoolResearchSynthesizer.parse(response, "Compare facts", [{"evidence_id": "E1", "source_locator": {"canonical_uri": "https://example.com/reference"}}])
    assert "Supported fact" in report.report_markdown
    assert "unsupported API rule" not in report.report_markdown
    assert "[E:E1]" in report.report_markdown and "https://example.com/reference" in report.report_markdown
    assert "unsupported API rule" in response["content"]


def test_synthesis_cannot_hide_missing_named_subject_in_free_narrative():
    import json

    response = {"content": json.dumps({"title": "R", "report_markdown": "AlphaGroup and BetaQueue are present here only.",
        "claims": [{"statement": "AlphaGroup supported fact", "evidence_ids": ["E1"]}]})}
    with pytest.raises(ValueError, match="betaqueue"):
        ResourcePoolResearchSynthesizer.parse(response, "Compare `AlphaGroup` and `BetaQueue`", [{"evidence_id": "E1"}])


def test_synthesis_can_render_current_claim_only_response_without_duplicate_body():
    import json

    report = ResourcePoolResearchSynthesizer.parse({"content": json.dumps({"title": "R", "claims": [
        {"statement": "Fact", "evidence_ids": ["E1"]}]})}, "Describe facts", [{"evidence_id": "E1"}])
    assert report.report_markdown.startswith("# R")
    assert "Fact [E:E1]" in report.report_markdown


@pytest.mark.parametrize("count", [10, 15, 16, 17])
def test_claim_inventory_is_bounded_without_truncating_cited_facts(count):
    import json

    response = {"content": json.dumps({"title": "R", "claims": [
        {"statement": f"Supported fact {index}", "evidence_ids": ["E1"]} for index in range(count)]})}
    if count > 16:
        with pytest.raises(ValueError, match="claim bound"):
            ResourcePoolResearchSynthesizer.parse(response, "Compare facts", [{"evidence_id": "E1"}])
    else:
        report = ResourcePoolResearchSynthesizer.parse(response, "Compare facts", [{"evidence_id": "E1"}])
        assert len(report.claims) == count
        assert f"Supported fact {count-1}" in report.report_markdown


async def test_claims_to_html_retains_conditions_types_and_exceptions_for_another_topic():
    import json
    from html import escape

    from web_artifacts.generator import WebArtifactGenerator

    statements = [
        'YouthPass is available only to visitors under 18; visitors under 12 require a guardian.',
        'ResidentPass requires local residency proof; temporary addresses are not accepted.',
        'All admission passes require advance booking and must not be used on closure days.',
        'The quota property is a number, while reserve() is an action; quota is not a method.',
    ]
    evidence = [{'evidence_id': f'p{i}', 'excerpt': value,
                 'source_locator': {'canonical_uri': 'https://example.invalid/policy', 'excerpt': value}}
                for i, value in enumerate(statements)]

    class Pool:
        calls = 0

        async def generate(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                payload = json.loads(next(message['content'] for message in kwargs['messages'] if message['role'] == 'user'))
                assert [item['excerpt'] for item in payload['evidence']] == statements
                assert all('excerpt' not in item['source_locator'] for item in payload['evidence'])
                return SimpleNamespace(content=json.dumps({'title': 'Admission policy', 'claims': [
                    {'statement': value, 'evidence_ids': [f'p{i}']} for i, value in enumerate(statements)
                ]}))
            payload = json.loads(kwargs['messages'][0]['content'].splitlines()[-1])
            assert payload['previous_html'] == '<html><body>No restrictions</body></html>'
            for value in statements:
                assert value in payload['source_markdown']
            # A deterministic adapter tests the transfer, not model reasoning.
            return SimpleNamespace(content='<html><head></head><body><button>Filter</button><pre>'
                                   + escape(payload['source_markdown']) + '</pre></body></html>')

    pool = Pool()
    report = await ResourcePoolResearchSynthesizer(pool).synthesize('Compare YouthPass ResidentPass', evidence)
    assert [claim.statement for claim in report.claims] == statements
    html = await WebArtifactGenerator(pool).generate(
        source_markdown=report.report_markdown, instruction='Use tabs',
        previous_html='<html><body>No restrictions</body></html>')
    for i, value in enumerate(statements):
        assert escape(value) in html
        assert f'[E:p{i}]' in html
    assert pool.calls == 2


def test_long_discovery_brief_becomes_bounded_search_without_changing_objective():
    from research_adapters.web import discovery_query
    original = 'Research CSS Flexbox and Grid using developer.mozilla.org. ' + 'Only use official documentation and preserve all conditions. ' * 20
    result = discovery_query(original, SourceStrategy())
    assert len(result) <= 240
    assert 'site:developer.mozilla.org' in result
    assert 'flexbox' in result and 'grid' in result
    assert 'preserve' not in result
    assert original.endswith('conditions. ')
    assert discovery_query('Museum admission policy', SourceStrategy()) == 'Museum admission policy'


def test_long_discovery_can_use_other_subjects_and_explicit_domain_preferences():
    from research_adapters.web import discovery_query
    result = discovery_query('Compare `admission` and `membership`. ' + 'Please explain all requirements. ' * 20,
                             SourceStrategy(preferred_domains=('museum.example',)))
    assert 'site:museum.example' in result and 'admission' in result and 'membership' in result
    assert len(result) <= 240


async def test_discovery_prioritizes_explicit_site_hints_in_long_objectives():
    brief = 'Research `admission` policy on docs.museum.example. ' + 'Preserve the relevant qualifications. ' * 20
    def handler(request):
        assert 'site:docs.museum.example' in request.url.params['q']
        return httpx.Response(200, json={'results': [
            {'url': 'https://other.example/irrelevant'},
            {'url': 'https://docs.museum.example/admission'},
            {'url': 'https://docs.museum.example/membership'},
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await SearXNGDiscoveryProvider('http://search', client=client).discover(brief, strategy=SourceStrategy(), limit=2)
    assert all(r.uri.startswith('https://docs.museum.example/') for r in results)
    assert all(r.metadata['preferred_domain'] for r in results)


@pytest.mark.parametrize('days,time_range', [(None, None), (0, None), (7, 'month'), (1, 'day'), (90, 'year')])
async def test_search_freshness_default_and_frozen_policy(days, time_range):
    strategy = SourceStrategy.from_dict({} if days is None else {'freshness_days': days})
    def handler(request):
        assert request.url.params.get('time_range') == time_range
        return httpx.Response(200, json={'results': [], 'unresponsive_engines': [['google', 'access denied']]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await SearXNGDiscoveryProvider('http://search', client=client).discover('specific query', strategy=strategy, limit=3)
    assert results.diagnostics['query'] == 'specific query'
    assert results.diagnostics['freshness_days'] == (days or 0)
    assert results.diagnostics['upstream_errors'][0]['engine'] == 'google'


def test_explicit_freshness_never_treats_unknown_or_old_dates_as_current():
    from datetime import UTC, datetime, timedelta

    from research_adapters.web import freshness_status, source_date
    strategy = SourceStrategy(freshness_days=7)
    assert freshness_status(SourceCandidate('https://example.org'), strategy) == 'date_unknown'
    assert freshness_status(SourceCandidate('https://example.org', published_at=datetime.now(UTC)-timedelta(days=8)), strategy) == 'outside_interval'
    assert freshness_status(SourceCandidate('https://example.org', published_at=datetime.now(UTC)-timedelta(days=2)), strategy) == 'confirmed'
    assert source_date('not a date') is None
    assert SourceStrategy.from_dict({'freshness_days': 7}).freshness_days == 7


async def test_search_diagnostics_distinguish_empty_partial_and_all_unavailable():
    from research_adapters.web import DiscoveryCandidates
    class Provider:
        def __init__(self, status):
            self.status = status
        async def discover(self, *args, **kwargs):
            return DiscoveryCandidates([], diagnostics={'status': self.status})
    for statuses, expected in [(['ok'], 'ok'), (['ok', 'all_unavailable'], 'partial_unavailable'), (['all_unavailable'], 'all_unavailable')]:
        result = await CompositeSourceDiscoveryProvider([Provider(s) for s in statuses]).discover('query', strategy=SourceStrategy(), limit=3)
        assert result.diagnostics['status'] == expected


def test_long_query_retains_lowercase_compound_directives():
    from research_adapters.web import discovery_query
    query = discovery_query('Compare HTTP Cache-Control no-cache no-store max-age. ' + 'Preserve all documented conditions and restrictions. ' * 15, SourceStrategy(preferred_domains=('developer.mozilla.org',)))
    assert all(term in query for term in ('no-cache', 'no-store', 'max-age', 'cache-control'))
    assert len(query) <= 240


@pytest.mark.parametrize('value', [True, 7.5, '7', None])
def test_source_strategy_requires_an_integer_freshness(value):
    with pytest.raises(ValueError, match='freshness_days'):
        SourceStrategy.from_dict({'freshness_days': value})
