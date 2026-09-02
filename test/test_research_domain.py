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
    assert config.hindsight.retain_mission
    assert config.hindsight.reflect_mission
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

    with pytest.raises(RuntimeError, match="all source discovery providers failed"):
        await CompositeSourceDiscoveryProvider([FailedProvider(), FailedProvider()]).discover(
            "topic", strategy=SourceStrategy(), limit=5
        )


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
