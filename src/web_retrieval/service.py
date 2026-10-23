"""Provider selection, hard scope, bounded diagnostics and shared source contracts."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from urllib.parse import urldefrag, urlsplit

import httpx

from research_adapters.deepseek import DeepSeekError
from research_adapters.web import freshness_status
from research_domain.models import SourceCandidate
from resources.run_budget import RunBudgetError

from .budget import RetrievalUsageUnknown
from .models import FetchRequest, FetchResponse, SearchRequest, SearchResponse
from .safety import RetrievalDenied, check_scope, domain_matches


def diagnostics(value: dict) -> dict:
    # Upstream diagnostics never contain source bodies, credentials or exception messages.
    encoded = json.dumps(value, default=str, ensure_ascii=False)
    if len(encoded) > 4000:
        return {'status': value.get('status'), 'detail': 'diagnostics_limit'}
    return value


class WebRetrievalService:
    def __init__(self, searches, fetches, *, default_search='legacy', default_fetch='local'):
        if default_search not in searches or default_fetch not in fetches:
            raise ValueError('retrieval_default_unavailable')
        self.searches, self.fetches = searches, fetches
        self.default_search, self.default_fetch = default_search, default_fetch

    async def search(self, request: SearchRequest) -> SearchResponse:
        provider = self.default_search if request.provider == 'auto' else request.provider
        if (provider not in self.searches or not request.query.strip()
                or type(request.limit) is not int or not 1 <= request.limit <= 50
                or not 0 < request.timeout <= 120):
            return SearchResponse('denied', diagnostics={'reason': 'invalid_search_request'})
        if provider != 'deepseek' and request.original_question is not None:
            return SearchResponse('denied', diagnostics={'reason': 'unsupported_original_question'})
        # Do not silently truncate explicit input or omit an unsupported hard requirement.
        if provider == 'deepseek' and (len(request.query) > 128 or
                request.original_question is not None and (
                    not request.original_question.strip() or len(request.original_question) > 128)):
            return SearchResponse('denied', diagnostics={'reason': 'deepseek_query_limit'})
        started = time.monotonic()
        try:
            async with asyncio.timeout(request.timeout):
                candidates = await self.searches[provider].discover(request.query,
                    strategy=request.strategy, limit=request.limit,
                    **({'original_question': request.original_question} if provider == 'deepseek' else {}))
        except RunBudgetError:
            raise
        except Exception as exc:
            unknown = isinstance(exc, (RetrievalUsageUnknown, httpx.TransportError, TimeoutError))
            return SearchResponse('denied' if isinstance(exc, PermissionError) else 'failed',
                diagnostics={'provider': provider, 'error': type(exc).__name__,
                             'usage_unknown': unknown or isinstance(exc, DeepSeekError) and exc.code == 'transport_error'})
        accepted: list[SourceCandidate] = []
        seen: set[str] = set()
        rejected: dict[str, int] = {}
        for candidate in candidates:
            try:
                host = check_scope(candidate.uri, request.strategy)
                reason = freshness_status(candidate, request.strategy)
                if reason not in {'unrestricted', 'confirmed'}:
                    raise RetrievalDenied(reason)
                path = urlsplit(candidate.uri).path.lower()
                basename = path.rsplit('/', 1)[-1]
                if (basename in {'genindex.html', 'py-modindex.html', 'search.html'}
                        or basename.endswith('-api-index.html')
                        or basename.startswith('genindex-') and basename.endswith('.html')):
                    raise RetrievalDenied('navigation_index')
                key = urldefrag(candidate.uri)[0]
                if key in seen:
                    raise RetrievalDenied('duplicate_url')
                seen.add(key)
                preferred = (bool(candidate.metadata.get('preferred_domain'))
                             or domain_matches(host, request.strategy.preferred_domains))
                accepted.append(replace(candidate, title=candidate.title[:500],
                    snippet=candidate.snippet[:4000], metadata={**candidate.metadata,
                    'preferred_domain': request.strategy.official_sources and preferred}))
            except RetrievalDenied as exc:
                reason = str(exc)
                rejected[reason] = rejected.get(reason, 0) + 1
        accepted.sort(key=lambda c: bool(c.metadata.get('preferred_domain')), reverse=True)
        upstream = getattr(candidates, 'diagnostics', {})
        unavailable = upstream.get('status') in {'all_unavailable', 'failed'}
        partial = bool(rejected) or upstream.get('status') in {'partial_unavailable', 'partial'}
        status = ('partial' if partial else 'ok') if accepted else ('failed' if unavailable else 'empty')
        return SearchResponse(status, tuple(accepted[:request.limit]), diagnostics({
            'provider': provider, 'query': request.query, 'rejected': rejected,
            'elapsed_ms': round((time.monotonic() - started) * 1000), 'upstream': upstream,
        }))

    async def fetch(self, request: FetchRequest) -> FetchResponse:
        provider = self.default_fetch if request.provider == 'auto' else request.provider
        if (provider not in self.fetches or request.format not in {'text', 'markdown'}
                or not 0 < request.timeout <= 120 or not 0 < request.max_bytes <= 2_000_000
                or request.browser_permission):
            return FetchResponse('denied', request.url, diagnostics={'reason': 'unsupported_fetch_request'})
        started = time.monotonic()
        try:
            check_scope(request.url, request.strategy)
            if provider == 'deepseek' and (request.require_final_url
                    or request.strategy.allowed_domains or request.strategy.excluded_domains):
                raise RetrievalDenied('final_url_unverifiable')
            if provider == 'deepseek' and request.format != 'markdown':
                raise RetrievalDenied('unsupported_format')
            candidate = request.candidate or SourceCandidate(request.url)
            if candidate.uri != request.url:
                raise RetrievalDenied('candidate_url_mismatch')
            async with asyncio.timeout(request.timeout):
                if provider == 'local':
                    content = await self.fetches[provider].fetch(candidate, strategy=request.strategy,
                        timeout=request.timeout, max_bytes=request.max_bytes,
                        **({'format': request.format} if request.format != 'text' else {}))
                else:
                    content = await self.fetches[provider].fetch(candidate)
            if len(content.text.encode('utf-8')) > request.max_bytes:
                raise RetrievalDenied('response_too_large')
            # Cloud Markdown stays exact: no lossy conversion or changed offsets.
            return FetchResponse('ok', request.url, content.metadata.get('final_url'), content,
                                 diagnostics={'provider': provider, 'format': content.metadata.get('content_format'),
                                     'elapsed_ms': round((time.monotonic() - started) * 1000)})
        except RunBudgetError:
            raise
        except (RetrievalDenied, PermissionError) as exc:
            return FetchResponse('denied', request.url, diagnostics={'reason': str(exc)[:80]})
        except Exception as exc:
            return FetchResponse('failed', request.url, diagnostics={'error': type(exc).__name__,
                'usage_unknown': isinstance(exc, (RetrievalUsageUnknown, httpx.TransportError, TimeoutError))
                    or isinstance(exc, DeepSeekError) and exc.code == 'transport_error'})
