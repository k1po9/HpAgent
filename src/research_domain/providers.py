"""Ports owned by HpAgent's Research source layer."""

from __future__ import annotations

from typing import Protocol

from .models import ResearchReport, SourceCandidate, SourceContent, SourceStrategy


class SourceCanonicalizer(Protocol):
    def canonicalize(self, uri: str) -> str: ...


class SourceDiscoveryProvider(Protocol):
    async def discover(
        self,
        query: str,
        *,
        strategy: SourceStrategy,
        limit: int,
    ) -> list[SourceCandidate]: ...


class BrowserFetchProvider(Protocol):
    async def fetch_html(self, uri: str) -> str: ...


class SourceContentProvider(Protocol):
    async def fetch(self, candidate: SourceCandidate) -> SourceContent: ...


class ResearchSynthesisProvider(Protocol):
    async def synthesize(
        self,
        objective: str,
        evidence: list[dict[str, object]],
    ) -> ResearchReport: ...
