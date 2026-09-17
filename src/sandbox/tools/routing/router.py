from collections.abc import Sequence

from .capability import CapabilityMatcher
from .models import ScoredCandidate, ToolExposure, ToolSelectionResult
from .policy import CandidatePolicy
from .projector import ToolSchemaProjector


class ToolRouter:
    def __init__(self, registry, semantic_retriever=None, *, semantic_fetch_limit: int = 10, hint_weight: float = .6, rrf_k: int = 60):
        self._registry = registry
        self._semantic = semantic_retriever
        self._fetch_limit = semantic_fetch_limit
        self._hint_weight = hint_weight
        self._rrf_k = rrf_k
        self._capability = CapabilityMatcher()
        self._policy = CandidatePolicy()
        self._projector = ToolSchemaProjector()

    async def select(self, *, query: str, hints: Sequence[str], runtime, final_limit: int) -> ToolSelectionResult:
        all_tools = self._registry.list_registered()
        decisions = {t.name: self._capability.evaluate(t, runtime) for t in all_tools}
        eligible = {t.name: t for t in all_tools if decisions[t.name].eligible}
        always = self._policy.select_always(eligible.values())
        front_doors = self._policy.select_front_doors(eligible.values())
        allowed = frozenset(t.name for t in eligible.values() if t.routing.exposure in {ToolExposure.SEMANTIC, ToolExposure.FRONT_DOOR})
        fused: dict[str, float] = {}
        raw: list[ScoredCandidate] = []
        queries = [(query, 1.0)] if query else []
        queries.extend((hint, self._hint_weight) for hint in hints if hint)
        if self._semantic is None:
            raw = [ScoredCandidate(name, 0.0, rank, query) for rank, name in enumerate(sorted(allowed), 1)]
            fused = {item.tool_name: 1 / (self._rrf_k + item.rank) for item in raw}
        else:
            for source_query, weight in queries:
                result = await self._semantic.retrieve(source_query, allowed_tool_names=allowed, limit=self._fetch_limit)
                for item in result.candidates:
                    raw.append(item)
                    fused[item.tool_name] = fused.get(item.tool_name, 0.0) + weight / (self._rrf_k + item.rank)
        semantic = tuple(ScoredCandidate(name, max((x.score for x in raw if x.tool_name == name), default=0.0), rank, "fused", score) for rank, (name, score) in enumerate(sorted(fused.items(), key=lambda x: (-x[1], x[0])), 1))
        selected = self._policy.merge(always=always, front_doors=front_doors, semantic=semantic, final_limit=final_limit)
        audit = {
            "mode": "capability_semantic_v2", "final_limit": final_limit,
            "runtime": {"surface": runtime.surface, "has_workspace": runtime.has_workspace, "has_run_file_scope": runtime.has_run_file_scope,
                "resources": [{"logical_name": r.logical_name, "scope": r.scope.value, "media_type": r.media_type, "extension": r.extension, "direction": r.direction} for r in runtime.resources]},
            "eligibility": {name: {"eligible": d.eligible, **({"reason": d.reason} if d.reason else {})} for name, d in decisions.items()},
            "always_candidates": always, "front_door_candidates": front_doors,
            "semantic_candidates": [{"tool": c.tool_name, "rank": c.rank, "score": c.score, "fused_score": c.fused_score} for c in semantic],
            "final_tools": selected, "tools": selected, "tool_count": len(selected), "queries": [q for q, _ in queries][:5],
        }
        return ToolSelectionResult(tuple(self._projector.project(self._registry.require(name)) for name in selected), audit)
