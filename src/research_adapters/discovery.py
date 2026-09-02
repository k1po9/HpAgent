"""GitHub/RSS discovery plus strategy-aware provider composition."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from research_domain.models import SourceCandidate, SourceStrategy
from research_domain.providers import SourceDiscoveryProvider


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
            )
        )
        merged: list[SourceCandidate] = []
        seen: set[str] = set()
        for candidates in results:
            for candidate in candidates:
                if candidate.uri in seen:
                    continue
                seen.add(candidate.uri)
                merged.append(candidate)
                if len(merged) == limit:
                    return merged
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
            candidates.append(
                SourceCandidate(
                    uri=str(value.get("html_url") or ""),
                    title=str(value.get("full_name") or value.get("name") or ""),
                    snippet=str(value.get("description") or ""),
                    publisher="GitHub",
                    provider="githubkit",
                    source_type="github_repository",
                    metadata={
                        "stars": value.get("stargazers_count"),
                        "language": value.get("language"),
                        "updated_at": str(value.get("updated_at") or ""),
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
                candidates.append(
                    SourceCandidate(
                        uri=str(entry.get("link") or ""),
                        title=str(entry.get("title") or ""),
                        snippet=str(entry.get("summary") or ""),
                        publisher=str(parsed.feed.get("title") or feed_uri),
                        published_at=published_at,
                        provider="feedparser",
                        source_type="rss_entry",
                        metadata={"feed_uri": feed_uri},
                    )
                )
                if len(candidates) == limit:
                    return [item for item in candidates if item.uri]
        return [item for item in candidates if item.uri]
