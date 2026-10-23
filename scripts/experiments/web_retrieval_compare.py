"""Small, paid, bounded provider comparison; no models or business database."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

from research_adapters.deepseek import DeepSeekWebProvider, public_web_url
from research_adapters.web import (
    SearXNGDiscoveryProvider,
    StaticWebContentProvider,
    W3libSourceCanonicalizer,
)
from research_domain.excerpts import evidence_spans, missing_concepts
from research_domain.models import SourceCandidate, SourceStrategy

ROOT = Path(__file__).resolve().parents[2]
LEDGER = Path('/home/hp/workspace/hpagent-retrieval-plan/runtime/paid-calls.jsonl')
CASES = [
    ('css', 'developer.mozilla.org', 'CSS Flexbox Grid dimensions axes wrapping',
     ['https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Flexible_box_layout/Basic_concepts',
      'https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Grid_layout/Relationship_with_other_layout_methods']),
    ('cache', 'developer.mozilla.org', 'HTTP Cache-Control no-cache no-store max-age private 304',
     ['https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control',
      'https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Caching']),
    ('asyncio', 'docs.python.org', 'asyncio TaskGroup gather Lock exceptions cancellation fairness',
     ['https://docs.python.org/3/library/asyncio-task.html',
      'https://docs.python.org/3/library/asyncio-sync.html']),
]


def reserve_attempt(operation: str) -> None:
    """Every attempt occupies one slot permanently, including failures/unknowns."""
    if LEDGER.is_symlink():
        raise RuntimeError('invalid_call_log')
    # The initial experiment already has a ledger. Never recreate missing history.
    try:
        stream = LEDGER.open('r+', encoding='utf-8')
    except FileNotFoundError:
        raise RuntimeError('missing_call_log') from None
    with stream:
        os.fchmod(stream.fileno(), 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        lines = stream.readlines()
        if not lines:
            raise RuntimeError('invalid_call_log')
        try:
            for index, line in enumerate(lines, 1):
                entry = json.loads(line)
                if (not isinstance(entry, dict) or type(entry.get('attempt')) is not int
                        or entry['attempt'] != index
                        or entry.get('operation') not in {'search', 'fetch'}
                        or not isinstance(entry.get('at'), str) or not line.endswith('\n')):
                    raise ValueError
                timestamp = datetime.fromisoformat(entry['at'])
                if timestamp.utcoffset() is None:
                    raise ValueError
        except (ValueError, TypeError):
            raise RuntimeError('invalid_call_log') from None
        if len(lines) >= 1000:
            raise RuntimeError('experiment_call_limit')
        if operation not in {'search', 'fetch'}:
            raise ValueError('invalid_call_operation')
        stream.write(json.dumps({'attempt': len(lines) + 1, 'operation': operation,
                                 'at': datetime.now(UTC).isoformat()}) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


async def official_only(request: httpx.Request) -> None:
    public_web_url(str(request.url))
    if request.url.host not in {'developer.mozilla.org', 'docs.python.org'}:
        raise ValueError('comparison_domain_denied')


class BoundedComparisonFetcher(StaticWebContentProvider):
    """Bound the old fetcher's download for this fixed-domain experiment."""

    async def _get(self, client, uri):
        async with client.stream('GET', uri) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ValueError('response_too_large')
            return httpx.Response(response.status_code, content=bytes(body),
                                  headers={'content-type': response.headers.get('content-type', '')},
                                  request=response.request)


async def run(args):
    # Read only the selected secret; do not export or log the entire dotenv.
    key = os.environ.get('DEEPSEEK_API_SEARCH_FETCH') or dotenv_values(ROOT / '.env').get('DEEPSEEK_API_SEARCH_FETCH')
    if not key:
        raise ValueError('missing_deepseek_search_fetch_key')
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    cloud = DeepSeekWebProvider(key, before_request=reserve_attempt, timeout_seconds=30)
    search = SearXNGDiscoveryProvider(args.searxng_url, max_attempts=1, timeout_seconds=20)
    # Fixed public official domains only; no browser or arbitrary discovered URL fetches.
    async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                event_hooks={'request': [official_only]}) as client:
        local = BoundedComparisonFetcher(W3libSourceCanonicalizer(), client=client, max_attempts=1)
        cloud_enabled = True
        for name, domain, topic, urls in CASES:
            query = f'site:{domain} {topic}'
            strategy = SourceStrategy(github=False, rss=False, preferred_domains=(domain,))
            for provider, adapter in [('searxng', search), ('deepseek', cloud)]:
                if provider == 'deepseek' and not cloud_enabled:
                    continue
                started = time.monotonic()
                record = {'case': name, 'operation': 'search', 'provider': provider, 'query': query}
                try:
                    candidates = await adapter.discover(query, strategy=strategy, limit=5)
                    record.update(diagnostics=getattr(candidates, 'diagnostics', {}), candidates=[
                        {'url': c.uri, 'title': c.title, 'snippet': c.snippet,
                         'official': urlsplit(c.uri).hostname == domain} for c in candidates])
                except Exception as exc:
                    record['error'] = getattr(exc, 'code', type(exc).__name__)
                    if getattr(exc, 'status', None) in {401, 402, 403}:
                        cloud_enabled = False
                record['elapsed_ms'] = round((time.monotonic() - started) * 1000)
                records.append(record)
            # Known URLs deliberately isolate Fetch quality from search recall.
            for index, uri in enumerate(urls):
                for provider, adapter in [('local', local), ('deepseek', cloud)]:
                    if provider == 'deepseek' and not cloud_enabled:
                        continue
                    record = {'case': name, 'operation': 'fetch', 'provider': provider,
                              'url': uri, 'selection': 'fixed_official_baseline'}
                    started = time.monotonic()
                    try:
                        content = await asyncio.wait_for(adapter.fetch(SourceCandidate(uri)), 40)
                        spans = evidence_spans(content.text, topic)
                        filename = f'{name}-{index}-{provider}.txt'
                        (args.output / filename).write_text(content.text)
                        record.update(chars=len(content.text), sha256=content.content_hash,
                                      body=filename, evidence_spans=len(spans),
                                      missing_concepts=missing_concepts(topic, [content.text[a:b] for a, b, _ in spans]),
                                      metadata=content.metadata)
                    except Exception as exc:
                        record['error'] = getattr(exc, 'code', type(exc).__name__)
                        if getattr(exc, 'status', None) in {401, 402, 403}:
                            cloud_enabled = False
                    record['elapsed_ms'] = round((time.monotonic() - started) * 1000)
                    records.append(record)
                    (args.output / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
    (args.output / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
    print(json.dumps({'output': str(args.output), 'records': len(records),
                      'cumulative_attempts': len(LEDGER.read_text().splitlines()) if LEDGER.exists() else 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Authorize up to 9 paid attempts this run')
    parser.add_argument('--searxng-url', default='http://127.0.0.1:8085')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if not options.execute or not options.output.is_absolute():
        parser.error('--execute and an absolute new --output directory are required')
    asyncio.run(run(options))
