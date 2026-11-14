"""QQ ambient-text limits only; formal messages and Web never call this code."""
from __future__ import annotations

import json

from common.token_counter import estimate_tokens

MARKER = ' ...'
DEFAULT_MESSAGE_TOKENS = 512


def encoded_content_bytes(text: str) -> int:
    """Redis cjson escapes slashes; exclude the two JSON string quotes."""
    return len(json.dumps(text, ensure_ascii=False).replace('/', '\\/').encode('utf-8')) - 2


def excerpt(text: str, budget: int, *, max_bytes: int | None = None) -> str:
    """Keep a Unicode prefix; both limits include the single visible suffix."""
    def fits(value: str) -> bool:
        return (estimate_tokens(value) <= budget
                and (max_bytes is None or encoded_content_bytes(value) <= max_bytes))

    if fits(text):
        return text
    if not fits(MARKER):
        return ''
    source = text[:-len(MARKER)] if text.endswith(MARKER) else text
    lo, hi = 0, len(source)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(source[:mid] + MARKER):
            lo = mid
        else:
            hi = mid - 1
    return source[:lo] + MARKER
