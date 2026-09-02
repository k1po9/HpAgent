"""SearXNG discovery and static-first web content adapters."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from research_domain.models import (
    SourceCandidate,
    SourceContent,
    SourceStrategy,
    content_sha256,
)
from research_domain.providers import BrowserFetchProvider


class W3libSourceCanonicalizer:
    def canonicalize(self, uri: str) -> str:
        from w3lib.url import canonicalize_url

        return cast(str, canonicalize_url(uri, keep_fragments=False))


class SearXNGDiscoveryProvider:
    """Translate SearXNG JSON into the stable HpAgent candidate contract."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.max_attempts = max_attempts

    async def discover(
        self,
        query: str,
        *,
        strategy: SourceStrategy,
        limit: int,
    ) -> list[SourceCandidate]:
        if not query.strip() or limit <= 0 or not strategy.public_web:
            return []
        params: dict[str, Any] = {"q": query, "format": "json", "language": "auto"}
        if strategy.freshness_days <= 1:
            params["time_range"] = "day"
        elif strategy.freshness_days <= 31:
            params["time_range"] = "month"
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.timeout_seconds, follow_redirects=True
        )
        try:
            response = await self._get(client, f"{self.base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()
        finally:
            if own_client:
                await client.aclose()
        candidates: list[SourceCandidate] = []
        preferred = {domain.lower().removeprefix("www.") for domain in strategy.preferred_domains}
        for result in payload.get("results", []):
            uri = str(result.get("url") or "").strip()
            if not uri:
                continue
            hostname = (urlsplit(uri).hostname or "").lower().removeprefix("www.")
            preferred_domain = strategy.official_sources and any(
                hostname == domain or hostname.endswith(f".{domain}") for domain in preferred
            )
            candidates.append(
                SourceCandidate(
                    uri=uri,
                    title=str(result.get("title") or ""),
                    snippet=str(result.get("content") or ""),
                    provider="searxng",
                    source_type="web",
                    metadata={
                        "engine": result.get("engine"),
                        "engines": result.get("engines", []),
                        "score": result.get("score"),
                        "preferred_domain": preferred_domain,
                    },
                )
            )
        candidates.sort(key=lambda item: bool(item.metadata.get("preferred_domain")), reverse=True)
        return candidates[:limit]

    async def _get(self, client: httpx.AsyncClient, uri: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return await client.get(uri, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(0.1 * (attempt + 1))
        assert last_error is not None
        raise last_error


class PlaywrightBrowserFetchProvider:
    """Launch Chromium only for a single fallback fetch, then close it."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_ms = int(timeout_seconds * 1000)

    @staticmethod
    def _launch_options() -> dict[str, Any]:
        options: dict[str, Any] = {"headless": True}
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        if not proxy:
            return options
        bypass = ",".join(
            item.strip()
            for item in (os.getenv("NO_PROXY") or "").split(",")
            if item.strip()
        )
        proxy_options = {"server": proxy}
        if bypass:
            proxy_options["bypass"] = bypass
        options["proxy"] = proxy_options
        return options

    async def fetch_html(self, uri: str) -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(**self._launch_options())
            try:
                page = await browser.new_page()
                await page.goto(uri, wait_until="domcontentloaded", timeout=self.timeout_ms)
                return cast(str, await page.content())
            finally:
                await browser.close()


class StaticWebContentProvider:
    """Use httpx+Trafilatura first and invoke Playwright only when insufficient."""

    def __init__(
        self,
        canonicalizer: W3libSourceCanonicalizer,
        *,
        browser: BrowserFetchProvider | None = None,
        timeout_seconds: float = 20.0,
        min_content_chars: int = 240,
        client: httpx.AsyncClient | None = None,
        extractor=None,
        max_attempts: int = 2,
    ) -> None:
        self.canonicalizer = canonicalizer
        self.browser = browser
        self.timeout_seconds = timeout_seconds
        self.min_content_chars = min_content_chars
        self.client = client
        self.extractor = extractor or self._extract
        self.max_attempts = max_attempts

    async def fetch(self, candidate: SourceCandidate) -> SourceContent:
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "HpAgent-Research/1.0"},
        )
        used_browser = False
        try:
            response = await self._get(client, candidate.uri)
            response.raise_for_status()
            html = response.text
            final_uri = str(response.url)
            mime_type = response.headers.get("content-type", "text/html").split(";", 1)[0]
        except (httpx.TransportError, httpx.HTTPStatusError):
            if self.browser is None:
                raise
            html = await self.browser.fetch_html(candidate.uri)
            final_uri = candidate.uri
            mime_type = "text/html"
            used_browser = True
        finally:
            if own_client:
                await client.aclose()
        text = self.extractor(html, final_uri)
        if len(text) < self.min_content_chars and self.browser is not None and not used_browser:
            rendered_html = await self.browser.fetch_html(final_uri)
            rendered_text = self.extractor(rendered_html, final_uri)
            if len(rendered_text) > len(text):
                text = rendered_text
                used_browser = True
        if not text:
            raise ValueError("web source did not contain extractable text")
        canonical_uri = self.canonicalizer.canonicalize(final_uri)
        return SourceContent(
            canonical_uri=canonical_uri,
            title=candidate.title,
            text=text,
            content_hash=content_sha256(text),
            publisher=candidate.publisher,
            published_at=candidate.published_at,
            mime_type=mime_type,
            metadata={
                "fetch_mode": "playwright" if used_browser else "httpx",
                "fetched_at": datetime.now(UTC).isoformat(),
            },
        )

    async def _get(self, client: httpx.AsyncClient, uri: str) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return await client.get(uri)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(0.1 * (attempt + 1))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _extract(html: str, uri: str) -> str:
        import trafilatura

        return (
            trafilatura.extract(
                html,
                url=uri,
                output_format="txt",
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            or ""
        ).strip()
