"""Strongly typed contracts used by capability-first tool routing."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.tools import BaseTool


class ToolKind(str, Enum):
    NATIVE = "native"
    MCP = "mcp"
    SKILL = "skill"


class ToolExposure(str, Enum):
    ALWAYS = "always_if_eligible"
    FRONT_DOOR = "front_door"
    SEMANTIC = "semantic"
    INTERNAL = "internal"


class ResourceScope(str, Enum):
    NONE = "none"
    WORKSPACE = "workspace"
    CURRENT_RUN = "current_run"


@dataclass(frozen=True)
class ToolRoutingSpec:
    capability: str
    exposure: ToolExposure = ToolExposure.SEMANTIC
    resource_scope: ResourceScope = ResourceScope.NONE
    requires_workspace: bool = False
    requires_run_file_scope: bool = False
    accepts_extensions: frozenset[str] = field(default_factory=frozenset)
    accepts_media_types: frozenset[str] = field(default_factory=frozenset)
    required_services: frozenset[str] = field(default_factory=frozenset)
    front_door_family: str | None = None
    front_door_priority: int = 0

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("routing capability must not be empty")
        object.__setattr__(self, "accepts_extensions", frozenset(x.lower().lstrip(".") for x in self.accepts_extensions))
        object.__setattr__(self, "accepts_media_types", frozenset(x.lower() for x in self.accepts_media_types))


@dataclass(frozen=True)
class RegisteredTool:
    tool: BaseTool
    kind: ToolKind
    routing: ToolRoutingSpec

    @property
    def name(self) -> str:
        return self.tool.name


@dataclass(frozen=True)
class ResourceFact:
    resource_id: str
    logical_name: str
    scope: ResourceScope
    media_type: str | None
    extension: str | None
    direction: str | None = None


@dataclass(frozen=True)
class RuntimeCapabilitySnapshot:
    surface: str
    has_workspace: bool
    has_run_file_scope: bool
    resources: tuple[ResourceFact, ...] = ()
    available_services: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    reason: str | None = None


@dataclass(frozen=True)
class ScoredCandidate:
    tool_name: str
    score: float
    rank: int
    source_query: str
    fused_score: float = 0.0


@dataclass(frozen=True)
class SemanticRetrievalResult:
    candidates: tuple[ScoredCandidate, ...] = ()


@dataclass(frozen=True)
class ToolSelectionResult:
    tool_schemas: tuple[dict[str, Any], ...]
    audit: dict[str, Any]
