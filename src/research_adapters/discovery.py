"""GitHub/RSS discovery plus strategy-aware provider composition."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from research_domain.models import SourceCandidate, SourceStrategy
from research_domain.providers import SourceDiscoveryProvider

logger = logging.getLogger(__name__)


def _is_preferred_uri(uri: str, preferred_domains: tuple[str, ...]) -> bool:
    hostname = (urlsplit(uri).hostname or "").lower().removeprefix("www.")
    domains = {domain.lower().removeprefix("www.") for domain in preferred_domains}
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def _is_preferred_github_repository(
    full_name: str, preferred_domains: tuple[str, ...]
) -> bool:
    owner = full_name.partition("/")[0].casefold()
    normalized = {
        value.casefold().removeprefix("https://").removeprefix("http://").rstrip("/")
        for value in preferred_domains
    }
    repository = full_name.casefold()
    return bool(owner) and bool(
        {owner, f"github.com/{owner}", repository, f"github.com/{repository}"} & normalized
    )


class CompositeSourceDiscoveryProvider:
    def __init__(self, providers: list[SourceDiscoveryProvider]) -> None:
        self.providers = providers

    async def discover(
        self, query: str, *, strategy: SourceStrategy, limit: int
    ) -> list[SourceCandidate]:
        if limit <= 0:
            return []
        results = await asyncio.gather(
            *(
                provider.discover(query, strategy=strategy, limit=limit)
                for provider in self.providers
            ),
            return_exceptions=True,
        )
        failures: list[BaseException] = []
        provider_results: list[list[SourceCandidate]] = []
        for provider, result in zip(self.providers, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(result)
                logger.warning(
                    "source discovery provider failed",
                    extra={
                        "provider": type(provider).__name__,
                        "error_type": type(result).__name__,
                    },
                )
                provider_results.append([])
            else:
                provider_results.append(result)
        if failures and len(failures) == len(self.providers):
            raise RuntimeError("all source discovery providers failed") from failures[0]

        merged: list[SourceCandidate] = []
        seen: set[str] = set()
        position = 0
        while len(merged) < limit:
            added = False
            for candidates in provider_results:
                if position >= len(candidates):
                    continue
                added = True
                candidate = candidates[position]
                if candidate.uri in seen:
                    continue
                seen.add(candidate.uri)
                merged.append(candidate)
                if len(merged) == limit:
                    return merged
            if not added:
                break
            position += 1
        return merged


class GitHubDiscoveryProvider:
    """Use githubkit's generated Search REST client and return repository records."""

    def __init__(self, client: Any | None = None, *, token: str | None = None) -> None:
        self.client = client
        self.token = token

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        from githubkit import GitHub
        from githubkit.auth import TokenAuthStrategy

        token = self.token or os.getenv("GITHUB_TOKEN")
        self.client = GitHub(auth=TokenAuthStrategy(token)) if token else GitHub()
        return self.client

    async def discover(
        self, query: str, *, strategy: SourceStrategy, limit: int
    ) -> list[SourceCandidate]:
        if not strategy.github or not query.strip() or limit <= 0:
            return []
        response = await self._client().rest.search.async_repos(q=query, per_page=min(limit, 100))
        payload = response.parsed_data
        items = payload.items if hasattr(payload, "items") else payload.get("items", [])
        candidates: list[SourceCandidate] = []
        for item in items[:limit]:
            value = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            uri = str(value.get("html_url") or "")
            full_name = str(value.get("full_name") or value.get("name") or "")
            candidates.append(
                SourceCandidate(
                    uri=uri,
                    title=full_name,
                    snippet=str(value.get("description") or ""),
                    publisher="GitHub",
                    provider="githubkit",
                    source_type="github_repository",
                    metadata={
                        "stars": value.get("stargazers_count"),
                        "language": value.get("language"),
                        "updated_at": str(value.get("updated_at") or ""),
                        "preferred_domain": strategy.official_sources
                        and _is_preferred_github_repository(
                            full_name, strategy.preferred_domains
                        ),
                    },
                )
            )
        return [candidate for candidate in candidates if candidate.uri]


class RSSDiscoveryProvider:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def discover(
        self, query: str, *, strategy: SourceStrategy, limit: int
    ) -> list[SourceCandidate]:
        if not strategy.rss or not strategy.rss_feeds or limit <= 0:
            return []
        import feedparser

        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "HpAgent-Research/1.0"},
        )
        try:
            responses = await asyncio.gather(*(client.get(uri) for uri in strategy.rss_feeds))
        finally:
            if own_client:
                await client.aclose()
        terms = {term.casefold() for term in query.split() if len(term) > 2}
        candidates: list[SourceCandidate] = []
        for feed_uri, response in zip(strategy.rss_feeds, responses, strict=True):
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            for entry in parsed.entries:
                haystack = f"{entry.get('title', '')} {entry.get('summary', '')}".casefold()
                if terms and not any(term in haystack for term in terms):
                    continue
                published_at = None
                parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
                if parsed_time:
                    published_at = datetime(
                        parsed_time.tm_year,
                        parsed_time.tm_mon,
                        parsed_time.tm_mday,
                        parsed_time.tm_hour,
                        parsed_time.tm_min,
                        parsed_time.tm_sec,
                        tzinfo=UTC,
                    )
                uri = str(entry.get("link") or "")
                candidates.append(
                    SourceCandidate(
                        uri=uri,
                        title=str(entry.get("title") or ""),
                        snippet=str(entry.get("summary") or ""),
                        publisher=str(parsed.feed.get("title") or feed_uri),
                        published_at=published_at,
                        provider="feedparser",
                        source_type="rss_entry",
                        metadata={
                            "feed_uri": feed_uri,
                            "preferred_domain": strategy.official_sources
                            and (
                                _is_preferred_uri(uri, strategy.preferred_domains)
                                or _is_preferred_uri(
                                    feed_uri, strategy.preferred_domains
                                )
                            ),
                        },
                    )
                )
                if len(candidates) == limit:
                    return [item for item in candidates if item.uri]
        return [item for item in candidates if item.uri]
