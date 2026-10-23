"""Small semantic-body adapter around BeautifulSoup and markdownify."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from markdownify import ATX, MarkdownConverter


class MarkdownExtractionError(ValueError):
    """An explicit reason to reuse the already-downloaded legacy text path."""


_NOISE = (
    'script, style, nav, aside, form, button, [hidden], [aria-hidden="true"], '
    '[role="navigation"], [role="complementary"], [role="banner"], [role="contentinfo"]'
)


def _fenced(text: str, language: str = '') -> str:
    fence = '`' * max(3, 1 + max((len(m[0]) for m in re.finditer(r'`+', text)), default=0))
    return f'{fence}{language}\n{text}' + ('' if text.endswith('\n') else '\n') + fence


def _language(element) -> str:
    for node in [element, *element.find_all('code', recursive=False)]:
        for name in node.get('class', []):
            match = re.fullmatch(r'(?:language-|lang-|highlight-)([A-Za-z0-9_+-]+)', name)
            if match:
                return match[1]
    return ''


class _TechnicalMarkdown(MarkdownConverter):
    # Generic adaptations only; no website-specific definitions or placeholders.
    def convert_pre(self, el, text, parent_tags):
        return '\n\n' + _fenced(el.get_text(), _language(el)) + '\n\n'

    def convert_dd(self, el, text, parent_tags):
        # Definition-list extensions are not universal Markdown. Keep paragraphs
        # in place without indenting the entire body (including fenced code).
        return '\n\n' + text.strip() + '\n\n'

    def convert_table(self, el, text, parent_tags):
        if el.select('[rowspan], [colspan], table, pre, br') or any(
            '\n' in code.get_text() for code in el.find_all('code')
        ):
            for node in [el, *el.find_all(True)]:
                node.attrs = {k: v for k, v in node.attrs.items() if k in {'rowspan', 'colspan', 'scope'}}
            return '\n\nComplex table (original HTML cell spans/blocks):\n\n' + _fenced(str(el), 'html') + '\n\n'
        return super().convert_table(el, text, parent_tags)


def extract_markdown(html: str, uri: str) -> str:
    """Convert one unambiguous semantic body, never the full page by default.

    Definitions, headings and ordinary tables are handled by markdownify itself.
    No placeholder restoration, website selectors or synthetic API headings.
    """
    soup = BeautifulSoup(html, 'html.parser')
    for selector in ('main', '[role="main"]', 'article'):
        bodies = soup.select(selector)
        if bodies:
            if len(bodies) != 1:
                raise MarkdownExtractionError('ambiguous_body')
            body = bodies[0]
            break
    else:
        raise MarkdownExtractionError('body_unavailable')
    for node in body.select(_NOISE):
        if node.parent is not None:
            node.decompose()
    # Resolve against the complete verified URL, including its directory/version.
    for node in body.select('a[href], img[src]'):
        key = 'href' if node.name == 'a' else 'src'
        try:
            target = urljoin(uri, node[key])
            if urlsplit(target).scheme.lower() not in {'http', 'https'}:
                raise ValueError('unsupported_link_scheme')
            node[key] = target
        except ValueError:
            del node[key]
    value = _TechnicalMarkdown(heading_style=ATX, bullets='-', strip_pre=None).convert_soup(body).strip()
    if not value:
        raise MarkdownExtractionError('empty_markdown')
    return value
