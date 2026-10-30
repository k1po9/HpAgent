"""Trusted Research execution parameters stored inside the existing definition."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, fields

from common.config_validation import typed_fields


@dataclass(frozen=True)
class ResearchPolicy:
    synthesis_prompt: str = field(repr=False)
    max_sources: int = 30
    max_fetches: int = 20
    max_iterations: int = 3  # Existing gap diagnostic; graph remains three rounds.
    min_evidence: int = 3
    min_distinct_sources: int = 2
    search_provider: str = "legacy"
    fetch_provider: str = "local"
    search_timeout_seconds: float = 15.0
    fetch_timeout_seconds: float = 20.0
    model_selector: str = "chat"
    max_tokens: int = 4096
    graph_version: str = "research-three-rounds-v1"
    extractor_version: str = "research-passages-v2"
    history_version: str = "research-history-v1"
    check_version: str = "research.basic/v1"

    def __post_init__(self):
        typed_fields(type(self), asdict(self), "research_policy")
        for name in ("max_sources", "max_fetches", "max_iterations", "min_evidence", "min_distinct_sources"):
            if getattr(self, name) <= 0:
                raise ValueError(f"research_policy.{name}: must be positive")
        for name in ("search_timeout_seconds", "fetch_timeout_seconds"):
            if not 0 < getattr(self, name) <= 120:
                raise ValueError(f"research_policy.{name}: must be within (0, 120]")
        if self.search_provider not in {"legacy", "deepseek"}:
            raise ValueError("research_policy.search_provider: unsupported provider")
        if self.fetch_provider not in {"local", "deepseek"}:
            raise ValueError("research_policy.fetch_provider: unsupported provider")
        if not self.synthesis_prompt.strip() or len(self.synthesis_prompt.encode()) > 16384:
            raise ValueError("research_policy.synthesis_prompt: expected nonempty text within 16384 bytes")
        for name in ("model_selector", "max_tokens", "graph_version", "extractor_version", "history_version", "check_version"):
            if getattr(self, name) != getattr(type(self), name):
                raise ValueError(f"research_policy.{name}: unsupported implementation contract")

    def to_snapshot(self) -> dict:
        return {"schema_version": 1, **asdict(self),
                "prompt_sha256": hashlib.sha256(self.synthesis_prompt.encode()).hexdigest()}

    @classmethod
    def from_snapshot(cls, value: object) -> ResearchPolicy:
        names = {f.name for f in fields(cls)}
        if (not isinstance(value, dict) or set(value) != names | {"schema_version", "prompt_sha256"}
                or type(value.get("schema_version")) is not int or value["schema_version"] != 1):
            raise ValueError("research_policy: invalid snapshot fields or version")
        policy = cls(**{name: value[name] for name in names})
        if value["prompt_sha256"] != policy.to_snapshot()["prompt_sha256"]:
            raise ValueError("research_policy.prompt_sha256: snapshot mismatch")
        return policy
