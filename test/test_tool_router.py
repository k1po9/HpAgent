from __future__ import annotations

import asyncio
import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from sandbox.tools.registry import ToolRegistry
from sandbox.tools.routing.capability import CapabilityMatcher
from sandbox.tools.routing.models import (
    ResourceFact, ResourceScope, RuntimeCapabilitySnapshot, ScoredCandidate,
    SemanticRetrievalResult, ToolExposure, ToolRoutingSpec,
)
from sandbox.tools.routing.router import ToolRouter


class EmptyInput(BaseModel):
    pass


def _tool(name: str):
    async def call() -> str:
        return name
    return StructuredTool.from_function(name=name, description=f"Use {name}", args_schema=EmptyInput, coroutine=call)


def _snapshot(extension: str | None, *, workspace: bool = True):
    resources = () if extension is None else (
        ResourceFact("1", f"input.{extension}", ResourceScope.CURRENT_RUN, None, extension, "input"),
    )
    return RuntimeCapabilitySnapshot("web", workspace, extension is not None, resources)


def _registered(name: str, spec: ToolRoutingSpec):
    registry = ToolRegistry()
    registry.register(_tool(name), routing=spec)
    return registry.require(name)


@pytest.mark.parametrize(
    ("extension", "expected"),
    [("txt", {"read_file"}),
     ("pdf", {"read_file", "read_pdf_pages"}),
     ("xlsx", {"read_file", "read_sheet_range"})],
)
def test_capability_filters_file_specializations(extension, expected):
    specs = {
        "read_file": ToolRoutingSpec("file.read", ToolExposure.FRONT_DOOR, ResourceScope.CURRENT_RUN,
                                      requires_run_file_scope=True, accepts_extensions=frozenset({"txt", "pdf", "xlsx"})),
        "read_pdf_pages": ToolRoutingSpec("file.pdf.pages", resource_scope=ResourceScope.CURRENT_RUN,
                                           requires_run_file_scope=True, accepts_extensions=frozenset({"pdf"})),
        "read_sheet_range": ToolRoutingSpec("file.xlsx.range", resource_scope=ResourceScope.CURRENT_RUN,
                                             requires_run_file_scope=True, accepts_extensions=frozenset({"xlsx"})),
    }
    matcher = CapabilityMatcher()
    eligible = {name for name, spec in specs.items() if matcher.evaluate(_registered(name, spec), _snapshot(extension)).eligible}
    assert eligible == expected


def test_workspace_capability_is_independent_from_run_files():
    tool = _registered("fs_read", ToolRoutingSpec("workspace.read", requires_workspace=True, resource_scope=ResourceScope.WORKSPACE))
    matcher = CapabilityMatcher()
    assert matcher.evaluate(tool, _snapshot("txt", workspace=True)).eligible
    assert matcher.evaluate(tool, _snapshot("txt", workspace=False)).reason == "workspace_unavailable"


class RecordingRetriever:
    def __init__(self):
        self.allowed = []

    async def retrieve(self, query, *, allowed_tool_names, limit):
        self.allowed.append((query, allowed_tool_names))
        ordered = [name for name in ("read_pdf_pages", "weather", "read_file") if name in allowed_tool_names]
        return SemanticRetrievalResult(tuple(ScoredCandidate(name, 1.0, rank, query) for rank, name in enumerate(ordered, 1)))


@pytest.mark.asyncio
async def test_router_retrieves_only_eligible_and_reserves_front_door():
    registry = ToolRegistry()
    registry.register(_tool("read_file"), routing=ToolRoutingSpec(
        "file.read", ToolExposure.FRONT_DOOR, ResourceScope.CURRENT_RUN,
        requires_run_file_scope=True, accepts_extensions=frozenset({"txt", "pdf"}),
        front_door_family="current_run_file", front_door_priority=100,
    ))
    registry.register(_tool("read_pdf_pages"), routing=ToolRoutingSpec(
        "file.pdf.pages", resource_scope=ResourceScope.CURRENT_RUN,
        requires_run_file_scope=True, accepts_extensions=frozenset({"pdf"}),
    ))
    registry.register(_tool("weather"), routing=ToolRoutingSpec("weather.lookup"))
    registry.freeze()
    retriever = RecordingRetriever()
    result = await ToolRouter(registry, retriever).select(
        query="weather", hints=(), runtime=_snapshot("txt"), final_limit=2,
    )
    assert retriever.allowed[0][1] == frozenset({"read_file", "weather"})
    assert result.audit["front_door_candidates"] == ["read_file"]
    assert result.audit["final_tools"] == ["read_file", "weather"]
    schema = result.tool_schemas[0]["function"]
    assert "next_tool_hint" in schema["parameters"]["properties"]
    assert "next_tool_hint" not in schema["parameters"].get("required", [])


@pytest.mark.asyncio
async def test_concurrent_selection_keeps_audits_request_local():
    registry = ToolRegistry()
    for name in ("alpha", "beta"):
        registry.register(_tool(name), routing=ToolRoutingSpec(f"test.{name}"))
    registry.freeze()

    class ConcurrentRetriever:
        async def retrieve(self, query, *, allowed_tool_names, limit):
            await asyncio.sleep(0)
            return SemanticRetrievalResult((ScoredCandidate(query, .9, 1, query),))

    router = ToolRouter(registry, ConcurrentRetriever())
    a, b = await asyncio.gather(
        router.select(query="alpha", hints=(), runtime=_snapshot(None), final_limit=1),
        router.select(query="beta", hints=(), runtime=_snapshot(None), final_limit=1),
    )
    assert a.audit["final_tools"] == ["alpha"]
    assert b.audit["final_tools"] == ["beta"]


def test_registry_rejects_missing_contract_and_name_collision():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="missing ToolRoutingSpec"):
        registry.register(_tool("alpha"))
    registry.register(_tool("alpha"), routing=ToolRoutingSpec("test.alpha"))
    with pytest.raises(ValueError, match="duplicate tool name"):
        registry.register(_tool("alpha"), category="mcp", routing=ToolRoutingSpec("mcp.test.alpha"))
