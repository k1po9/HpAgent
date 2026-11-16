from collections.abc import Sequence

from .capability import CapabilityMatcher
from .models import (
    ScoredCandidate,
    SemanticRetrievalResult,
    ToolExposure,
    ToolRetrievalError,
    ToolRoutingConfigurationError,
    ToolSelectionResult,
    retrieval_failure,
)
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
        audit = {
            "mode": "capability_semantic_v2", "final_limit": final_limit,
            "rag_enabled": self._semantic is not None, "reasons": [],
            "ordinary_candidate_count": 0, "final_tools": [], "tools": [], "tool_count": 0,
        }
        try:
            return await self._select(query=query, hints=hints, runtime=runtime, final_limit=final_limit, audit=audit)
        except Exception as exc:
            error = retrieval_failure("selection", exc)
            audit.update(status="failed", final_tools=[], tools=[], tool_count=0)
            audit["reasons"].append({"stage": error.stage, "code": error.code})
            return ToolSelectionResult((), audit, "failed", error)

    async def _select(self, *, query, hints, runtime, final_limit, audit):
        if final_limit <= 0 or self._fetch_limit <= 0 or self._rrf_k < 0:
            raise ToolRoutingConfigurationError("tool limits must be positive and rrf_k nonnegative")
        all_tools = self._registry.list_registered()
        decisions = {t.name: self._capability.evaluate(t, runtime) for t in all_tools}
        eligible = {t.name: t for t in all_tools if decisions[t.name].eligible}
        always = self._policy.select_always(eligible.values())
        front_doors = self._policy.select_front_doors(eligible.values())
        # Validate mandatory capacity even when retrieval is down or no query is supplied.
        self._policy.merge(always=always, front_doors=front_doors, semantic=(), final_limit=final_limit)
        allowed = frozenset(t.name for t in eligible.values() if t.routing.exposure in {ToolExposure.SEMANTIC, ToolExposure.FRONT_DOOR})
        fused: dict[str, float] = {}
        raw: list[ScoredCandidate] = []
        queries = [(query, 1.0)] if query else []
        queries.extend((hint, self._hint_weight) for hint in hints if hint)
        status = "success" if always or allowed else "no_eligible_tools"
        last_error = None
        fallback = self._semantic is None or not queries
        if self._semantic is not None and allowed:
            for source_query, weight in queries:
                try:
                    result = await self._semantic.retrieve(source_query, allowed_tool_names=allowed, limit=self._fetch_limit)
                except Exception as exc:
                    error = retrieval_failure("semantic", exc)
                    result = SemanticRetrievalResult(status="degraded" if error.recoverable else "failed", error=error)
                if result.status == "failed":
                    raise result.error or ToolRetrievalError("semantic", "retrieval_failed")
                if result.status == "degraded":
                    if result.error is None or not result.error.recoverable:
                        raise result.error or ToolRetrievalError("semantic", "invalid_degradation")
                    status, last_error = "degraded", result.error
                    audit["reasons"].append({"stage": result.error.stage, "code": result.error.code})
                    # A reranker failure retains valid Chroma ranking. Other dependency
                    # failures use the current allowed set, merged by the same policy.
                    if not result.candidates:
                        fallback = True
                for item in result.candidates:
                    if item.tool_name not in allowed:
                        raise PermissionError("semantic candidate is not allowed")
                    if item.rank <= 0:
                        raise ValueError("semantic candidate rank must be positive")
                    raw.append(item)
                    fused[item.tool_name] = fused.get(item.tool_name, 0.0) + weight / (self._rrf_k + item.rank)
        if fallback:
            raw = [ScoredCandidate(name, 0.0, rank, query) for rank, name in enumerate(sorted(allowed), 1)]
            fused = {item.tool_name: 1 / (self._rrf_k + item.rank) for item in raw}
        semantic = tuple(ScoredCandidate(name, max((x.score for x in raw if x.tool_name == name), default=0.0), rank, "fused", score) for rank, (name, score) in enumerate(sorted(fused.items(), key=lambda x: (-x[1], x[0])), 1))
        selected = self._policy.merge(always=always, front_doors=front_doors, semantic=semantic, final_limit=final_limit)
        audit.update({
            "status": status,
            "runtime": {"surface": runtime.surface, "has_workspace": runtime.has_workspace, "has_run_file_scope": runtime.has_run_file_scope,
                "resources": [{"logical_name": r.logical_name, "scope": r.scope.value, "media_type": r.media_type, "extension": r.extension, "direction": r.direction} for r in runtime.resources]},
            "eligibility": {name: {"eligible": d.eligible, **({"reason": d.reason} if d.reason else {})} for name, d in decisions.items()},
            "always_candidates": always, "front_door_candidates": front_doors,
            "semantic_candidates": [{"tool": c.tool_name, "rank": c.rank, "score": c.score, "fused_score": c.fused_score} for c in semantic],
            "ordinary_candidate_count": len(selected),
            "final_tools": selected, "tools": selected, "tool_count": len(selected), "queries": [q for q, _ in queries][:5],
        })
        return ToolSelectionResult(tuple(self._projector.project(self._registry.require(name)) for name in selected), audit, status, last_error)
