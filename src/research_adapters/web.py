"""SearXNG discovery and static-first web content adapters."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
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


def discovery_query(objective: str, strategy: SourceStrategy, *, max_chars: int = 240) -> str:
    """Bound long research briefs into lexical search terms, not factual answers.

    Keep short explicit searches intact. The complete objective remains in the
    persisted research plan and synthesis; no task constraint is discarded there.
    Site hints influence discovery only and never grant network access.
    """
    # Scheduling metadata is not a search concept. Keep subject prose intact.
    clean = re.sub(r'\b\d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]+)?\b|\bUTC(?:[+-]\d{1,2}(?::\d{2})?)?\b', ' ', objective)
    compact = ' '.join(clean.split())
    if len(compact) <= max_chars:
        return compact
    from research_domain.excerpts import named_concepts, objective_terms

    terms = named_concepts(compact) or objective_terms(compact)
    domains = strategy.allowed_domains or strategy.preferred_domains or tuple(dict.fromkeys(
        re.findall(r'(?<![\w.-])(?:[a-zA-Z0-9-]+\.){2,}[a-zA-Z]{2,}(?![\w-])', compact)
    ))[:2]
    sites = ' OR '.join('site:' + domain for domain in domains[:2])
    # Preserve compound technical identifiers, including lowercase directives.
    compounds = tuple(word.casefold() for word in re.findall(r'[A-Za-z][A-Za-z0-9_]*(?:[-.][A-Za-z0-9_]+)+', compact)
                      if word.casefold() not in {domain.casefold() for domain in domains})
    terms = tuple(dict.fromkeys((*compounds, *terms)))
    prefix = f'({sites}) ' if sites else ''
    selected: list[str] = []
    for term in terms[:16]:
        if len(prefix + ' '.join([*selected, term])) <= max_chars:
            selected.append(term)
    return prefix + ' '.join(selected) if selected else compact[:max_chars]


class DiscoveryCandidates(list[SourceCandidate]):
    """List-compatible candidates with request-local, bounded search diagnostics."""

    def __init__(self, candidates=(), *, diagnostics=None):
        super().__init__(candidates)
        self.diagnostics = diagnostics or {}


def source_date(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def freshness_status(candidate: SourceCandidate, strategy: SourceStrategy) -> str:
    if not strategy.freshness_days:
        return "unrestricted"
    published = candidate.published_at
    if published is None:
        return "date_unknown"
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    return "confirmed" if now - timedelta(days=strategy.freshness_days) <= published <= now else "outside_interval"


class SearXNGDiscoveryProvider:
    """Translate SearXNG JSON into the stable HpAgent candidate contract."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 2,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.max_attempts = max_attempts
        self.max_response_bytes = max_response_bytes

    async def discover(
        self,
        query: str,
        *,
        strategy: SourceStrategy,
        limit: int,
    ) -> list[SourceCandidate]:
        if not query.strip() or limit <= 0 or not strategy.public_web:
            return []
        search = discovery_query(query, strategy)
        params: dict[str, Any] = {"q": search, "format": "json", "language": "auto"}
        if 0 < strategy.freshness_days <= 1:
            params["time_range"] = "day"
        elif 0 < strategy.freshness_days <= 31:
            params["time_range"] = "month"
        elif strategy.freshness_days > 31:
            params["time_range"] = "year"
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.timeout_seconds, follow_redirects=True
        )
        try:
            response = await self._get(client, f"{self.base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return DiscoveryCandidates([], diagnostics={
                "query": search, "freshness_days": strategy.freshness_days,
                "time_range": params.get("time_range"), "candidate_count": 0,
                "status": "all_unavailable", "upstream_errors": [{"error": type(exc).__name__}],
            })
        finally:
            if own_client:
                await client.aclose()
        candidates: list[SourceCandidate] = []
        domain_hints = strategy.preferred_domains or tuple(re.findall(r"site:([a-zA-Z0-9.-]+)", search))
        preferred = {domain.lower().removeprefix("www.") for domain in domain_hints}
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
                    published_at=source_date(result.get("publishedDate")),
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
        unavailable = [{"engine": str(item[0])[:80], "error": str(item[1])[:160]}
                       for item in payload.get("unresponsive_engines", [])[:20] if len(item) >= 2]
        status = "partial_unavailable" if unavailable else "ok"
        return DiscoveryCandidates(candidates[:limit], diagnostics={
            "query": search, "freshness_days": strategy.freshness_days,
            "time_range": params.get("time_range"), "candidate_count": len(candidates),
            "status": status, "upstream_errors": unavailable,
        })

    async def _get(self, client: httpx.AsyncClient, uri: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                async with client.stream('GET', uri, **kwargs) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_response_bytes:
                            raise ValueError('search_response_too_large')
                    return httpx.Response(response.status_code, content=bytes(body),
                                          headers={"content-type": response.headers.get("content-type", "application/json")},
                                          request=response.request)
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
    def _extract_markdown(html: str, uri: str) -> str:
        from research_adapters.markdown import extract_markdown

        return extract_markdown(html, uri)

    @staticmethod
    def _extract(html: str, uri: str) -> str:
        import trafilatura
        from lxml import html as lxml_html

        # Article extraction often drops API definition signatures (<dt>) while
        # retaining their prose. Preserve the semantic main document when it owns
        # definition lists; otherwise keep the general article extractor.
        try:
            root = lxml_html.fromstring(html, parser=lxml_html.HTMLParser(no_network=True))
            mains = root.xpath('//main | //*[@role="main"]')
            if mains and mains[0].xpath('.//dl//dt'):
                main = mains[0]
                for hidden in main.xpath('.//script | .//style | .//nav | .//aside | .//header | .//footer | .//form'):
                    hidden.drop_tree()
                blocks = {'main', 'section', 'article', 'div', 'p', 'pre', 'blockquote',
                          'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'dl', 'dt', 'dd', 'ul', 'ol', 'li', 'tr'}
                def text_parts(element):
                    if not isinstance(element.tag, str):
                        return
                    if element.tag in blocks:
                        yield '\n\n'
                    if element.text:
                        yield element.text
                    for child in element:
                        yield from text_parts(child)
                        if child.tail:
                            yield child.tail
                    if element.tag in blocks:
                        yield '\n\n'
                value = ''.join(text_parts(main))
                value = '\n'.join(' '.join(line.split()) for line in value.splitlines())
                value = re.sub(r'\n{3,}', '\n\n', value).strip()
                if value:
                    return value
        except (ValueError, lxml_html.etree.ParserError):
            pass

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
