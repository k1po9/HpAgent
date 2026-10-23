"""Existing Research provider ports backed by the process-local shared service."""
from .models import FetchRequest, SearchRequest


class ResearchRetrievalAdapter:
    def __init__(self, service, *, search_timeout=30, fetch_timeout=30):
        self.service = service
        self.search_timeout, self.fetch_timeout = search_timeout, fetch_timeout

    async def discover(self, query, *, strategy, limit):
        from research_adapters.web import DiscoveryCandidates, discovery_query

        provider = self.service.default_search
        query = discovery_query(query, strategy, max_chars=128 if provider == 'deepseek' else 240)
        result = await self.service.search(SearchRequest(query, strategy, limit=min(limit, 50),
                                                       timeout=self.search_timeout))
        return DiscoveryCandidates(result.candidates, diagnostics={**result.diagnostics,
            'status': {'failed': 'all_unavailable', 'partial': 'partial_unavailable'}.get(result.status, result.status)})

    async def fetch(self, candidate):
        # Source scope is supplied by Research's persisted plan, not candidate metadata.
        from .context import source_scope

        result = await self.service.fetch(FetchRequest(candidate.uri, source_scope.get(),
            candidate=candidate, timeout=self.fetch_timeout, require_final_url=True))
        if result.content is None:
            raise ValueError(str(result.diagnostics.get('reason', result.status)))
        return result.content
