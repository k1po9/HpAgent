"""Validate each redirect, resolve all targets, then connect to a pinned public IP."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from research_adapters.deepseek import public_web_url
from research_adapters.web import StaticWebContentProvider, W3libSourceCanonicalizer
from research_domain.models import SourceCandidate, SourceContent, SourceStrategy, content_sha256


class RetrievalDenied(ValueError):
    pass


def domain_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d.lower().rstrip('.') or host.endswith('.' + d.lower().rstrip('.'))
               for d in domains)


def check_scope(uri: str, strategy: SourceStrategy) -> str:
    try:
        public_web_url(uri)
    except ValueError:
        raise RetrievalDenied('invalid_public_url') from None
    host = (urlsplit(uri).hostname or '').lower().rstrip('.').encode('idna').decode()
    if (strategy.allowed_domains and not domain_matches(host, strategy.allowed_domains)
            or domain_matches(host, strategy.excluded_domains)):
        raise RetrievalDenied('source_scope_denied')
    return host


async def resolve_public(host: str, port: int) -> str:
    addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(item[4][0] for item in addresses))
    if not ips or any((not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast) for ip in ips):
        raise RetrievalDenied('non_public_target')
    return ips[0]


class SafeLocalFetcher:
    """No proxy, retry or browser. DNS pinning retains original Host and TLS SNI."""
    def __init__(self, canonicalizer=None, *, resolver=resolve_public, transport=None,
                 extractor=None, markdown_extractor=None):
        self.canonicalizer = canonicalizer or W3libSourceCanonicalizer()
        self.resolver, self.transport = resolver, transport
        self.extractor = extractor or StaticWebContentProvider._extract
        self.markdown_extractor = markdown_extractor or StaticWebContentProvider._extract_markdown

    async def read_bytes(self, uri: str, *, strategy: SourceStrategy,
                         timeout: float, max_bytes: int):
        current = uri
        async with asyncio.timeout(timeout):
            for _ in range(6):
                host = check_scope(current, strategy)
                logical = httpx.URL(current)
                ip = await self.resolver(host, logical.port or (443 if logical.scheme == 'https' else 80))
                # Validate injected resolver results too. Never perform a second DNS lookup.
                if (not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast):
                    raise RetrievalDenied('non_public_target')
                target = logical.copy_with(host=ip)
                async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                             follow_redirects=False, timeout=timeout) as client:
                    async with client.stream('GET', target, headers={
                        'Host': logical.netloc.decode(), 'User-Agent': 'HpAgent-Research/1.0',
                    }, extensions={'sni_hostname': host}) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get('location')
                            if not location:
                                raise RetrievalDenied('invalid_redirect')
                            current = urljoin(current, location)
                            continue
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise RetrievalDenied('response_too_large')
                        html = httpx.Response(200, content=bytes(body), headers={
                            'content-type': response.headers.get('content-type', 'text/html')}).text
                        mime = response.headers.get('content-type', 'text/html').split(';')[0]
                return bytes(body), mime, current, html
        raise RetrievalDenied('redirect_limit')


    async def fetch(self, candidate: SourceCandidate, *, strategy: SourceStrategy,
                    timeout: float, max_bytes: int, format: str = 'text') -> SourceContent:
        from datetime import UTC, datetime

        if format not in {'text', 'markdown'}:
            raise ValueError('unsupported_format')
        _, mime, current, html = await self.read_bytes(candidate.uri, strategy=strategy,
                                                      timeout=timeout, max_bytes=max_bytes)
        requested_format = format
        fallback_reason = None
        if format == 'markdown':
            from research_adapters.markdown import MarkdownExtractionError

            try:
                text = self.markdown_extractor(html, current)
                if not text:
                    raise MarkdownExtractionError('empty_markdown')
            except Exception as exc:
                # Conversion only: reuse downloaded HTML, without another request.
                fallback_reason = str(exc) if isinstance(exc, MarkdownExtractionError) else 'conversion_error'
                format = 'text'
                text = self.extractor(html, current)
        else:
            text = self.extractor(html, current)
        if not text:
            raise ValueError('empty_content')
        if len(text.encode('utf-8')) > max_bytes:
            raise RetrievalDenied('response_too_large')
        return SourceContent(self.canonicalizer.canonicalize(current), candidate.title,
            text, content_sha256(text), publisher=candidate.publisher,
            published_at=candidate.published_at, mime_type=mime, metadata={
                'provider': 'local', 'fetch_mode': 'httpx', 'requested_url': candidate.uri,
                'final_url': current, 'final_url_verified': True, 'content_format': format,
                'extraction_format': 'markdown-v3' if format == 'markdown' else 'text-legacy',
                'extractor_version': 'local-markdown-v3' if format == 'markdown' else 'local-text-v1',
                **({'requested_format': requested_format, 'fallback_reason': fallback_reason}
                   if fallback_reason else {}),
                'received_at': datetime.now(UTC).isoformat(),
            })
