"""Strongly typed contracts used by capability-first tool routing."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from langchain_core.tools import BaseTool


class ToolRetrievalError(RuntimeError):
    """Classified retrieval failure with a safe stage/code and recovery flag."""

    def __init__(self, stage: str, code: str, *, recoverable: bool = False):
        self.recoverable = recoverable
        self.stage = stage
        self.code = "tool_retrieval_" + code
        super().__init__(self.code)


def retrieval_failure(stage: str, exc: Exception) -> ToolRetrievalError:
    """Classify only known transient dependency errors as recoverable."""
    import errno

    import httpx

    if isinstance(exc, ToolRetrievalError):
        return exc
    recoverable = isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException,
                                  httpx.NetworkError, httpx.RemoteProtocolError))
    if isinstance(exc, httpx.HTTPStatusError):
        recoverable = exc.response.status_code in {408, 429} or exc.response.status_code >= 500
        code = "permission_denied" if exc.response.status_code in {401, 403} else "dependency_http_error"
    elif isinstance(exc, PermissionError):
        code = "permission_denied"
    elif isinstance(exc, ToolRoutingConfigurationError):
        code = "configuration_invalid"
    else:
        code = "dependency_unavailable" if recoverable else "invalid_result_or_configuration"
    if isinstance(exc, OSError) and exc.errno in {errno.EIO, errno.EBUSY, errno.EAGAIN, errno.ENOSPC}:
        recoverable, code = True, "dependency_unavailable"
    error = ToolRetrievalError(stage, code, recoverable=recoverable)
    error.__cause__ = exc
    return error


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
    accepts_directions: frozenset[str] = field(default_factory=frozenset)
    required_services: frozenset[str] = field(default_factory=frozenset)
    front_door_family: str | None = None
    front_door_priority: int = 0

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("routing capability must not be empty")
        object.__setattr__(self, "accepts_extensions", frozenset(x.lower().lstrip(".") for x in self.accepts_extensions))
        object.__setattr__(self, "accepts_media_types", frozenset(x.lower() for x in self.accepts_media_types))
        directions = frozenset(x.lower() for x in self.accepts_directions)
        invalid = directions - {"input", "output"}
        if invalid:
            raise ValueError(f"invalid routing resource directions: {sorted(invalid)}")
        object.__setattr__(self, "accepts_directions", directions)


class ToolRoutingConfigurationError(ValueError):
    """The configured mandatory candidate set cannot satisfy router invariants."""


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


RetrievalStatus = Literal["success", "degraded", "no_eligible_tools", "failed"]


@dataclass(frozen=True)
class SemanticRetrievalResult:
    candidates: tuple[ScoredCandidate, ...] = ()
    status: RetrievalStatus = "success"
    error: ToolRetrievalError | None = None


@dataclass(frozen=True)
class ToolSelectionResult:
    tool_schemas: tuple[dict[str, Any], ...]
    audit: dict[str, Any]
    status: RetrievalStatus = "success"
    error: ToolRetrievalError | None = None
