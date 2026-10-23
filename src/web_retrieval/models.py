"""Bounded retrieval requests reuse the persisted Research source values."""
from dataclasses import dataclass, field
from typing import Any

from research_domain.models import SourceCandidate, SourceContent, SourceStrategy


@dataclass(frozen=True)
class SearchRequest:
    query: str
    strategy: SourceStrategy = field(default_factory=SourceStrategy)
    provider: str = 'auto'
    limit: int = 10
    original_question: str | None = None
    timeout: float = 30


@dataclass(frozen=True)
class FetchRequest:
    url: str
    strategy: SourceStrategy = field(default_factory=SourceStrategy)
    provider: str = 'auto'
    format: str = 'text'
    timeout: float = 30
    max_bytes: int = 2_000_000
    browser_permission: bool = False
    require_final_url: bool = False
    candidate: SourceCandidate | None = None


@dataclass(frozen=True)
class SearchResponse:
    status: str
    candidates: tuple[SourceCandidate, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchResponse:
    status: str
    requested_url: str
    final_url: str | None = None
    content: SourceContent | None = None
    truncated: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)
