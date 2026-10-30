"""Existing Research provider ports backed by the process-local shared service."""
from research_domain.context import research_policy

from .models import FetchRequest, SearchRequest


class ResearchRetrievalAdapter:
    def __init__(self, service, *, search_timeout=30, fetch_timeout=30):
        self.service = service
        self.search_timeout, self.fetch_timeout = search_timeout, fetch_timeout

    async def discover(self, query, *, strategy, limit):
        from research_adapters.web import DiscoveryCandidates, discovery_query

        policy = research_policy.get()
        provider = policy.search_provider if policy is not None else self.service.default_search
        if policy is not None and provider not in self.service.searches:
            raise ValueError("research_policy.search_provider: unavailable")
        query = discovery_query(query, strategy, max_chars=128 if provider == 'deepseek' else 240)
        result = await self.service.search(SearchRequest(query, strategy, provider=provider, limit=min(limit, 50),
            timeout=policy.search_timeout_seconds if policy is not None else self.search_timeout))
        return DiscoveryCandidates(result.candidates, diagnostics={**result.diagnostics,
            'status': {'failed': 'all_unavailable', 'partial': 'partial_unavailable'}.get(result.status, result.status)})

    async def fetch(self, candidate):
        # Source scope is supplied by Research's persisted plan, not candidate metadata.
        from .context import source_scope

        policy = research_policy.get()
        provider = policy.fetch_provider if policy is not None else "auto"
        if policy is not None and provider not in self.service.fetches:
            raise ValueError("research_policy.fetch_provider: unavailable")
        result = await self.service.fetch(FetchRequest(candidate.uri, source_scope.get(), provider=provider,
            candidate=candidate, timeout=policy.fetch_timeout_seconds if policy is not None else self.fetch_timeout,
            require_final_url=True))
        if result.content is None:
            raise ValueError(str(result.diagnostics.get('reason', result.status)))
        return result.content
