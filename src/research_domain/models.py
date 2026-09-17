"""Provider-neutral Research domain values.

Tool responses are deliberately not represented here.  Adapters translate their
transport payloads into ``SourceCandidate``/``SourceContent``; persistence then
creates ``SourceRecord`` and, separately, ``EvidenceItem`` rows.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SourceStrategy:
    public_web: bool = True
    official_sources: bool = True
    github: bool = True
    rss: bool = True
    uploaded_files: bool = False
    freshness_days: int = 7
    preferred_domains: tuple[str, ...] = ()
    rss_feeds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.freshness_days < 0:
            raise ValueError("freshness_days must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "public_web": self.public_web,
            "official_sources": self.official_sources,
            "github": self.github,
            "rss": self.rss,
            "uploaded_files": self.uploaded_files,
            "freshness_days": self.freshness_days,
            "preferred_domains": list(self.preferred_domains),
            "rss_feeds": list(self.rss_feeds),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "SourceStrategy":
        value = value or {}
        return cls(
            public_web=bool(value.get("public_web", True)),
            official_sources=bool(value.get("official_sources", True)),
            github=bool(value.get("github", True)),
            rss=bool(value.get("rss", True)),
            uploaded_files=bool(value.get("uploaded_files", False)),
            freshness_days=int(value.get("freshness_days", 7)),
            preferred_domains=tuple(str(item) for item in value.get("preferred_domains", ())),
            rss_feeds=tuple(str(item) for item in value.get("rss_feeds", ())),
        )


@dataclass(frozen=True)
class ResearchQuestion:
    id: str
    question: str
    priority: int = 1


@dataclass(frozen=True)
class ResearchPlan:
    objective: str
    questions: tuple[ResearchQuestion, ...]
    source_strategy: SourceStrategy
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "questions": [
                {"id": item.id, "question": item.question, "priority": item.priority}
                for item in self.questions
            ],
            "source_strategy": self.source_strategy.to_dict(),
            "version": self.version,
        }


@dataclass(frozen=True)
class SourceCandidate:
    uri: str
    title: str = ""
    snippet: str = ""
    publisher: str | None = None
    published_at: datetime | None = None
    provider: str = "unknown"
    source_type: str = "web"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceContent:
    canonical_uri: str
    title: str
    text: str
    content_hash: str
    author: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    mime_type: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    run_id: str
    provider: str
    source_type: str
    canonical_uri: str
    title: str
    author: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    mime_type: str | None = None
    language: str | None = None
    content_ref: str | None = None
    content_hash: str | None = None
    source_tier: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    run_id: str
    source_id: str
    excerpt: str
    content_ref: str
    source_locator: dict[str, Any]
    source_quality: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimDraft:
    statement: str
    evidence_ids: tuple[str, ...]
    importance: str = "normal"

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("claim statement must not be empty")
        if not self.evidence_ids:
            raise ValueError("claim must cite at least one EvidenceItem")
        if self.importance not in {"normal", "important"}:
            raise ValueError("unsupported claim importance")


@dataclass(frozen=True)
class ResearchReport:
    title: str
    report_markdown: str
    claims: tuple[ClaimDraft, ...]

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.report_markdown.strip():
            raise ValueError("research report title and markdown are required")
        if not self.claims:
            raise ValueError("research report must contain at least one claim")

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "report_markdown": self.report_markdown,
            "claims": [
                {
                    "statement": claim.statement,
                    "importance": claim.importance,
                    "evidence_ids": list(claim.evidence_ids),
                }
                for claim in self.claims
            ],
        }


def normalize_source_text(text: str) -> str:
    """Normalize insignificant whitespace before exact-content deduplication."""
    return re.sub(r"\s+", " ", text).strip()


def content_sha256(text: str) -> str:
    return hashlib.sha256(normalize_source_text(text).encode("utf-8")).hexdigest()
