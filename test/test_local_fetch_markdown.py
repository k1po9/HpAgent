"""Offline synthetic structure regression, not real MDN/Python page acceptance."""
import json
from pathlib import Path

import httpx
import pytest

from research_adapters.web import StaticWebContentProvider
from research_domain.excerpts import evidence_spans
from research_domain.models import SourceCandidate, content_sha256
from web_retrieval.models import FetchRequest
from web_retrieval.safety import SafeLocalFetcher
from web_retrieval.service import WebRetrievalService

CASES = json.loads((Path(__file__).parent / 'fixtures/research/local_fetch_html.json').read_text())
URI = 'https://docs.example.org/technical'


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_six_technical_shapes_preserve_definitions_examples_and_body_order(case):
    value = StaticWebContentProvider._extract_markdown(case['html'], URI)
    legacy = StaticWebContentProvider._extract(case['html'], URI)
    assert value.startswith('# ')
    assert '## ' in value
    assert 'Excluded' not in value
    for fact in case['facts']:
        assert fact in legacy
        assert fact in value
    for code in case['code']:
        assert code in value
        assert value.index(code) < value.index('additional background')
    assert '[the reference](https://example.org/reference)' in value
    if case['name'] == 'cache-control':
        assert '| Request | Response |' in value
        assert '| max-age | s-maxage |' in value
        assert '| no-cache | private |' in value
    if case['name'] == 'asyncio-task':
        assert value.index('asyncio.TaskGroup') < value.index('async def main():')
        assert value.index('print(task1.result())') < value.index('asyncio.gather')
        assert 'return_exceptions=False' in value
        assert r'return\_exceptions' not in value
        assert '        task1 = ' not in legacy  # known legacy indentation loss


def test_code_is_verbatim_with_tabs_blank_lines_entities_and_safe_fences():
    code = 'async def demo():\n    # ```` keep ticks\n\tawait work("<tag> & value")\n\n\n    return 1\n'
    html = '<main><h1>Code</h1><p>Before example.</p><pre class="language-python"><code>' + (
        code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')) + '</code></pre><p>After example.</p></main>'
    value = StaticWebContentProvider._extract_markdown(html, URI)
    assert '`````python\n' + code + '`````' in value
    assert value.index('Before example') < value.index(code) < value.index('After example')
    assert '\\(' not in value
    # A language is only assigned from explicit markup.
    plain = StaticWebContentProvider._extract_markdown('<main><pre>print(1)</pre></main>', URI)
    assert '```\nprint(1)\n```' in plain and '```python' not in plain


def test_definitions_headings_lists_links_and_noise_keep_their_order():
    html = '''<main><h1>API</h1><p>A technical guide for developers.</p>
    <h2>Mailbox</h2><dl><dt><em>class </em><code>example.Mailbox</code></dt>
    <dd><p>Delivers messages in order.</p><ul><li>First restriction</li><li>Second restriction</li></ul></dd></dl>
    <p>Read <a href="https://example.org/guide">the guide</a>.</p>
    <div hidden>ExcludedHidden</div><div role="navigation"><pre>ExcludedPre</pre></div>
    <script>ExcludedScript</script><style>ExcludedStyle</style></main>'''
    value = StaticWebContentProvider._extract_markdown(html, URI)
    assert '## Mailbox' in value
    assert '`example.Mailbox`' in value
    assert value.index('example.Mailbox') < value.index('Delivers') < value.index('First restriction')
    assert value.index('First restriction') < value.index('Second restriction') < value.index('[the guide]')
    assert 'Excluded' not in value


def test_complex_table_keeps_spans_and_multiline_cell_code_without_inventing_columns():
    html = '''<main><h1>Directive table</h1><p>The following table has merged cells.</p>
    <table onclick="bad()"><tr><th rowspan="2">Request</th><th>Response</th></tr>
    <tr><td><pre>first\n    second</pre></td></tr></table><p>After table.</p></main>'''
    value = StaticWebContentProvider._extract_markdown(html, URI)
    assert 'Complex table (original HTML cell spans/blocks)' in value
    assert '```html\n<table>' in value
    assert '<th rowspan="2">Request</th>' in value
    assert '<pre>first\n    second</pre>' in value
    assert 'onclick' not in value
    assert '| Request | Response |' not in value
    assert value.index('following table') < value.index('```html') < value.index('After table')


async def resolver(host, port):
    return '93.184.216.34'


def fetch_service(html, **kwargs):
    fetcher = SafeLocalFetcher(resolver=resolver, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=html)), **kwargs)
    return WebRetrievalService({'legacy': object()}, {'local': fetcher})


async def test_explicit_markdown_exact_source_hash_offsets_and_legacy_default():
    case = next(c for c in CASES if c['name'] == 'asyncio-sync')
    service = fetch_service(case['html'])
    old = await service.fetch(FetchRequest(URI))
    old_spans = evidence_spans(old.content.text, 'Lock fairness')
    old_snapshot = json.dumps({'text': old.content.text, 'hash': old.content.content_hash,
        'locators': [{'start': a, 'end': b, 'excerpt': old.content.text[a:b]}
                     for a, b, _ in old_spans]})
    new = await service.fetch(FetchRequest(URI, format='markdown'))
    assert old.status == new.status == 'ok'
    assert old.content.text == StaticWebContentProvider._extract(case['html'], URI)
    assert old.content.metadata['extraction_format'] == 'text-legacy'
    content = new.content
    assert content.metadata['content_format'] == 'markdown'
    assert content.metadata['extraction_format'] == 'markdown-v3'
    assert content.metadata['extractor_version'] == 'local-markdown-v3'
    assert content.metadata['final_url_verified'] is True
    assert content.content_hash == content_sha256(content.text)
    assert old.content.content_hash == content_sha256(old.content.text)
    assert old.content.content_hash != content.content_hash
    assert new.truncated is False
    selected = evidence_spans(content.text, 'Lock fairness')
    assert selected
    for start, end, _ in selected:
        excerpt = content.text[start:end]
        assert excerpt and excerpt in content.text
        assert 0 <= start < end <= len(content.text)
    assert any('Acquiring a lock is fair' in content.text[a:b] for a, b, _ in selected)
    # Existing serialized source/evidence remains on its original string.
    saved = json.loads(old_snapshot)
    assert saved['text'] == old.content.text and saved['hash'] == old.content.content_hash
    assert saved['locators']
    for locator in saved['locators']:
        assert saved['text'][locator['start']:locator['end']] == locator['excerpt']
    # New source can be serialized without an intervening Markdown/whitespace rewrite.
    saved_new = json.loads(json.dumps({'text': content.text, 'hash': content.content_hash}))
    assert saved_new['hash'] == content_sha256(saved_new['text'])
    for start, end, _ in selected:
        assert saved_new['text'][start:end] == content.text[start:end]


async def test_markdown_size_limit_is_not_hidden_by_text_fallback():
    calls = []
    def markdown(html, uri):
        calls.append('markdown')
        return 'x' * 201
    service = fetch_service('small input', markdown_extractor=markdown,
                            extractor=lambda *_: pytest.fail('unexpected legacy fallback'))
    result = await service.fetch(FetchRequest(URI, format='markdown', max_bytes=200))
    assert result.status == 'denied' and result.diagnostics['reason'] == 'response_too_large'
    assert calls == ['markdown']
    assert result.content is None and result.truncated is False


async def test_new_markdown_is_bounded_by_the_existing_main_output_contract():
    from agent_activities.web_tools import bound_text

    result = await fetch_service(CASES[0]['html']).fetch(FetchRequest(URI, format='markdown'))
    content = result.content
    output = bound_text({'hash': content.content_hash, 'format': 'markdown'}, content.text, 40, 1000)
    assert output['text'] == content.text[:40] and output['truncated'] is True
    assert output['hash'] == content_sha256(content.text)  # hash belongs to full body


async def test_research_adapter_still_requests_legacy_text_by_default():
    from web_retrieval.adapters import ResearchRetrievalAdapter

    class Service:
        async def fetch(self, request):
            assert request.format == 'text' and request.require_final_url is True
            return await fetch_service(CASES[0]['html']).fetch(request)

    content = await ResearchRetrievalAdapter(Service()).fetch(SourceCandidate(URI))
    assert content.metadata['content_format'] == 'text'


def test_unselected_pre_blocks_are_never_appended_to_body():
    html = '<nav><pre>OUTSIDE-CODE</pre></nav><main><h1>Body</h1><p>Selected body explanation.</p><pre>INSIDE-CODE</pre><p>End of selected body.</p></main>'
    value = StaticWebContentProvider._extract_markdown(html, URI)
    assert 'INSIDE-CODE' in value and 'OUTSIDE-CODE' not in value


@pytest.mark.parametrize('failure,reason', [('empty', 'empty_markdown'), ('error', 'conversion_error')])
async def test_conversion_failure_reuses_html_and_reports_actual_text_format(failure, reason):
    calls = []
    def markdown(html, uri):
        calls.append('markdown')
        if failure == 'error':
            raise RuntimeError('sensitive upstream/body details')
        return ''
    def legacy(html, uri):
        calls.append('text')
        assert html == 'downloaded once'
        return 'Stable legacy source body'
    service = fetch_service('downloaded once', markdown_extractor=markdown, extractor=legacy)
    service.fetches['local'].transport = httpx.MockTransport(
        lambda _: calls.append('get') or httpx.Response(200, text='downloaded once'))
    result = await service.fetch(FetchRequest(URI, format='markdown'))
    assert result.status == 'ok' and calls == ['get', 'markdown', 'text']
    assert result.content.text == 'Stable legacy source body'
    assert result.content.metadata['requested_format'] == 'markdown'
    assert result.content.metadata['content_format'] == result.diagnostics['format'] == 'text'
    assert result.content.metadata['extraction_format'] == 'text-legacy'
    assert result.content.metadata['fallback_reason'] == reason
    assert result.content.content_hash == content_sha256(result.content.text)


@pytest.mark.parametrize('html,reason', [
    ('<body><p>No main container.</p></body>', 'body_unavailable'),
    ('<main>One</main><main>Two</main>', 'ambiguous_body'),
])
async def test_missing_or_ambiguous_body_uses_stable_text(html, reason):
    service = fetch_service(html, extractor=lambda *_: 'Stable legacy text')
    result = await service.fetch(FetchRequest(URI, format='markdown'))
    assert result.status == 'ok' and result.content.metadata['fallback_reason'] == reason
    assert result.content.metadata['content_format'] == 'text'


def test_relative_links_resolve_against_full_verified_url_and_unsafe_schemes_are_dropped():
    value = StaticWebContentProvider._extract_markdown(
        '<main><h1>Links</h1><p><a href="#lock">Lock</a> '
        '<a href="threading.html#module-threading">Threads</a> '
        '<a href="javascript:bad()">Unsafe</a></p></main>',
        'https://docs.python.org/3/library/asyncio-sync.html')
    assert '[Lock](https://docs.python.org/3/library/asyncio-sync.html#lock)' in value
    assert '[Threads](https://docs.python.org/3/library/threading.html#module-threading)' in value
    assert 'Unsafe' in value and 'javascript:' not in value


@pytest.mark.parametrize('cells', [
    '<tr><td>Outer<table><tr><td>Nested</td></tr></table></td></tr>',
    '<tr><td><code>line1\n    line2</code></td></tr>',
])
def test_nested_table_and_multiline_inline_code_use_conservative_representation(cells):
    value = StaticWebContentProvider._extract_markdown(
        '<main><h1>Technical table</h1><p>Details follow.</p><table>' + cells + '</table></main>', URI)
    assert 'Complex table' in value and '```html' in value
    assert cells in value
    assert 'HPAGENTVERBATIM' not in value


def test_version_notes_and_relative_links_in_semantic_body_survive_conversion():
    value = StaticWebContentProvider._extract_markdown(
        '<main><h1>API</h1><h2>Sleeping</h2><div class="versionchanged">'
        '<p><span>Changed in version 3.13:</span> Raises <code>ValueError</code> '
        'if <code>delay</code> is <code>nan</code>.</p></div>'
        '<dl><dt><code>wait_for(aw, timeout)</code></dt><dd>'
        '<p>Changed in version 3.11: Raises <code>TimeoutError</code> instead of '
        '<code>asyncio.TimeoutError</code>.</p></dd></dl></main>', URI)
    assert '## Sleeping' in value
    assert 'Changed in version 3.13:' in value and '`nan`' in value
    assert 'Changed in version 3.11:' in value and '`asyncio.TimeoutError`' in value
