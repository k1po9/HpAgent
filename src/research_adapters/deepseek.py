"""DeepSeek Search/Fetch adapters; no retries, implicit credentials or workflow I/O."""
from __future__ import annotations

import inspect
import ipaddress
import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlsplit

import httpx

from research_adapters.web import DiscoveryCandidates, freshness_status, source_date
from research_domain.models import SourceCandidate, SourceContent, SourceStrategy, content_sha256


def public_web_url(uri: str) -> str:
    """Validate remote-fetch input; local fetch additionally needs connection controls."""
    if any(ord(char) <= 32 or ord(char) == 127 for char in uri):
        raise ValueError("invalid_public_url")
    parsed = urlsplit(uri)
    if (len(uri) > 8192 or parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 80, 443)):
        raise ValueError('invalid_public_url')
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('non_public_url')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host:
            raise ValueError('non_public_url') from None
    else:
        if not address.is_global or address.is_multicast:
            raise ValueError('non_public_url')
    return uri


class DeepSeekError(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(code)  # Never include request headers or upstream response body.


class DeepSeekWebProvider:
    """Implements existing discovery/content ports. Caller owns permission and quota.

    A before_request callback permits the experiment to enforce a durable hard cap.
    Production wiring must supply its existing trusted budget boundary before use.
    """
    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None,
                 timeout_seconds: float = 30, max_response_bytes: int = 2_000_000,
                 before_request: Callable[[str], Awaitable[None] | None] | None = None):
        if not api_key.strip():
            raise ValueError('missing_deepseek_search_fetch_key')
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError('positive_request_limits_required')
        self._key = api_key
        self.client = client
        self.timeout = timeout_seconds
        self.max_bytes = max_response_bytes
        self.before_request = before_request
        self.disabled_status: int | None = None

    async def _post(self, operation: str, payload: dict) -> dict:
        if self.disabled_status is not None:
            raise DeepSeekError("provider_stopped", self.disabled_status)
        from research_domain.context import research_policy
        policy = research_policy.get()
        timeout = policy.search_timeout_seconds if policy is not None else self.timeout
        client = self.client or httpx.AsyncClient()
        try:
            if self.before_request:
                pending = self.before_request(operation)
                if inspect.isawaitable(pending):
                    await pending
            async with client.stream(
                'POST', f'https://api.deepseek.com/v1/tools/{operation}',
                json=payload, headers={'Authorization': f'Bearer {self._key}'},
                timeout=timeout, follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    if response.status_code in {401, 402, 403}:
                        self.disabled_status = response.status_code
                    raise DeepSeekError(f'http_{response.status_code}', response.status_code)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self.max_bytes:
                        raise DeepSeekError('response_too_large')
                import json
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeError):
                    raise DeepSeekError('invalid_response') from None
                if not isinstance(value, dict):
                    raise DeepSeekError('invalid_response')
                return value
        except httpx.HTTPError:
            raise DeepSeekError('transport_error') from None
        finally:
            if self.client is None:
                await client.aclose()

    async def discover(self, query: str, *, strategy: SourceStrategy, limit: int,
                       original_question: str | None = None) -> list[SourceCandidate]:
        if not strategy.public_web or limit <= 0:
            return DiscoveryCandidates([], diagnostics={'status': 'disabled'})
        if not query.strip() or len(query) > 128 or type(limit) is not int or limit > 50:
            raise ValueError('invalid_search_input')
        if original_question is not None and (not original_question.strip() or len(original_question) > 128):
            raise ValueError('invalid_original_question')
        payload = {'query': query, 'count': limit, 'need_content': False}
        if original_question is not None:
            payload['ori_question'] = original_question
        started = time.monotonic()
        response = await self._post('search', payload)
        results = response.get('results')
        if not isinstance(results, list):
            raise DeepSeekError('invalid_response')
        candidates = []
        rejected = 0
        for rank, item in enumerate(results):
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ('url', 'title', 'snippet')):
                raise DeepSeekError('invalid_response')
            try:
                uri = public_web_url(item['url'])
            except ValueError:
                rejected += 1
                continue
            host = (urlsplit(uri).hostname or '').lower()
            candidate = SourceCandidate(
                uri=uri, title=item['title'], snippet=item['snippet'], provider='deepseek',
                publisher=item.get('site_name'), published_at=source_date(item.get('date')),
                metadata={'summary': str(item.get('summary') or '')[:4000],
                          'rank': rank, 'rerank_score': item.get('rerank_score'),
                          'authority': item.get('authority'),
                          'preferred_domain': strategy.official_sources and any(
                              host == d.lower() or host.endswith('.' + d.lower())
                              for d in strategy.preferred_domains)},
            )
            if freshness_status(candidate, strategy) in {'unrestricted', 'confirmed'}:
                candidates.append(candidate)
            else:
                rejected += 1
        return DiscoveryCandidates(candidates[:limit], diagnostics={
            'provider': 'deepseek', 'status': 'ok' if results else 'empty', 'query': query,
            'raw_count': len(results), 'rejected': rejected,
            'elapsed_ms': round((time.monotonic() - started) * 1000),
        })

    async def fetch(self, candidate: SourceCandidate) -> SourceContent:
        uri = public_web_url(candidate.uri)
        response = await self._post('fetch', {'url': uri})
        text, title = response.get('content'), response.get('title')
        if not isinstance(text, str) or not text.strip() or not isinstance(title, str):
            raise DeepSeekError('invalid_or_empty_content')
        # Keep exact Markdown as the persisted text: offsets refer to this exact string.
        return SourceContent(
            canonical_uri=uri, title=title, text=text, content_hash=content_sha256(text),
            publisher=candidate.publisher, published_at=candidate.published_at,
            mime_type='text/markdown', metadata={
                'provider': 'deepseek', 'content_format': 'markdown', 'requested_url': uri,
                'final_url': None, 'final_url_verified': False,
                'received_at': datetime.now(UTC).isoformat(),
            },
        )
