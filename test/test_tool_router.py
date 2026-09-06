from __future__ import annotations

import asyncio
import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from sandbox.tools.registry import ToolRegistry
from sandbox.tools.routing.capability import CapabilityMatcher
from sandbox.tools.routing.contracts import NATIVE_ROUTING_SPECS, routing_for
from sandbox.tools.routing.models import (
    ResourceFact, ResourceScope, RuntimeCapabilitySnapshot, ScoredCandidate,
    SemanticRetrievalResult, ToolExposure, ToolRoutingConfigurationError,
    ToolRoutingSpec,
)
from sandbox.tools.routing.policy import CandidatePolicy
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


@pytest.mark.parametrize(
    ("extension", "eligible", "ineligible"),
    [
        ("txt", {"read_file"}, {"replace_docx_text", "append_docx_section", "write_sheet_range", "replace_slide"}),
        ("docx", {"read_file", "replace_docx_text", "append_docx_section"}, {"write_sheet_range", "replace_slide"}),
        ("xlsx", {"read_file", "write_sheet_range"}, {"replace_docx_text", "replace_slide"}),
        ("pptx", {"read_file", "replace_slide"}, {"replace_docx_text", "write_sheet_range"}),
    ],
)
def test_native_file_patch_contract_matrix(extension, eligible, ineligible):
    matcher = CapabilityMatcher()
    runtime = _snapshot(extension)
    for name in eligible:
        assert matcher.evaluate(_registered(name, NATIVE_ROUTING_SPECS[name]), runtime).eligible
    for name in ineligible:
        assert not matcher.evaluate(_registered(name, NATIVE_ROUTING_SPECS[name]), runtime).eligible


def test_output_only_scope_excludes_legacy_input_analysis():
    runtime = RuntimeCapabilitySnapshot(
        "web", True, True,
        (ResourceFact("1", "generated.txt", ResourceScope.CURRENT_RUN, "text/plain", "txt", "output"),),
    )
    matcher = CapabilityMatcher()
    assert matcher.evaluate(_registered("read_file", NATIVE_ROUTING_SPECS["read_file"]), runtime).eligible
    for name in ("inspect_file", "search_file", "count_matches", "text_stats"):
        decision = matcher.evaluate(_registered(name, NATIVE_ROUTING_SPECS[name]), runtime)
        assert not decision.eligible
        assert decision.reason == "no_compatible_direction"


def test_direction_and_extension_must_match_same_resource():
    runtime = RuntimeCapabilitySnapshot(
        "web", True, True,
        (
            ResourceFact("1", "notes.txt", ResourceScope.CURRENT_RUN, "text/plain", "txt", "input"),
            ResourceFact("2", "report.pdf", ResourceScope.CURRENT_RUN, "application/pdf", "pdf", "output"),
        ),
    )
    spec = ToolRoutingSpec(
        "test.input_pdf", resource_scope=ResourceScope.CURRENT_RUN,
        requires_run_file_scope=True, accepts_extensions=frozenset({"pdf"}),
        accepts_directions=frozenset({"input"}),
    )
    decision = CapabilityMatcher().evaluate(_registered("input_pdf", spec), runtime)
    assert not decision.eligible
    assert decision.reason == "no_compatible_extension"


def test_gotenberg_service_and_real_input_formats_are_required():
    registered = _registered("convert_file_to_pdf", NATIVE_ROUTING_SPECS["convert_file_to_pdf"])
    matcher = CapabilityMatcher()
    unavailable = matcher.evaluate(registered, _snapshot("docx"))
    assert unavailable.reason == "required_service_unavailable"
    available = RuntimeCapabilitySnapshot(
        "web", True, True, _snapshot("docx").resources, frozenset({"gotenberg"}),
    )
    assert matcher.evaluate(registered, available).eligible
    wrong_format = RuntimeCapabilitySnapshot(
        "web", True, True, _snapshot("txt").resources, frozenset({"gotenberg"}),
    )
    assert matcher.evaluate(registered, wrong_format).reason == "no_compatible_extension"


def test_native_routing_is_exhaustive_and_directions_are_validated():
    with pytest.raises(ValueError, match="native tool 'calculator' has no explicit routing contract"):
        routing_for(_tool("calculator"), "native")
    with pytest.raises(ValueError, match="invalid routing resource directions"):
        ToolRoutingSpec("test.invalid", accepts_directions=frozenset({"sideways"}))


def test_frozen_registry_rejects_every_mutation():
    registry = ToolRegistry()
    registry.register(_tool("alpha"), routing=ToolRoutingSpec("test.alpha"))
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(_tool("beta"), routing=ToolRoutingSpec("test.beta"))
    with pytest.raises(RuntimeError, match="frozen"):
        registry.unregister("alpha")
    with pytest.raises(RuntimeError, match="frozen"):
        registry.clear()


def test_reserved_candidates_cannot_be_silently_truncated():
    with pytest.raises(ToolRoutingConfigurationError, match="reserved tools"):
        CandidatePolicy.merge(
            always=["mandatory"], front_doors=["read_file"], semantic=(), final_limit=1,
        )


def test_front_doors_are_ordered_by_priority_then_deterministic_ties():
    registry = ToolRegistry()
    for name, family, priority in (
        ("low", "zeta", 1), ("alpha", "alpha", 5), ("beta", "beta", 5),
        ("zeta_alias", "alpha", 5),
    ):
        registry.register(_tool(name), routing=ToolRoutingSpec(
            f"test.{name}", exposure=ToolExposure.FRONT_DOOR,
            front_door_family=family, front_door_priority=priority,
        ))
    assert CandidatePolicy.select_front_doors(registry.list_registered()) == ["alpha", "beta", "low"]
