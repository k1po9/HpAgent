"""All trusted entry adapters share discovery, definitions and bindings."""

from types import SimpleNamespace

import pytest

from agent_activities.composition import compose_capabilities
from research_activities.composition import compose_research
from research_domain.policy import ResearchPolicy
from web_artifacts.build import ArtifactBuildService


@pytest.mark.parametrize(
    "services", [frozenset(), frozenset({"html"}), frozenset({"research", "html"})]
)
def test_catalog_filters_services_without_changing_contracts(services):
    catalog = compose_capabilities(object(), services=services)
    names = {tool["function"]["name"] for tool in catalog.discover("chat")}
    assert ("capability__html__v1" in names) is ("html" in services)
    assert ("capability__research__v1" in names) is ("research" in services)
    for binding in catalog.bindings.values():
        definition = catalog.facts.registry.get(binding.key, binding.contract_version)
        assert definition["side_effect_policy"] == "candidate_output"
        assert definition["execution_mode"] == "isolated_execution"
    assert catalog.discover("unsupported") == []


def test_fixed_and_harness_adapters_use_the_same_catalog(monkeypatch):
    monkeypatch.delenv("FILE_STORE_ROOT", raising=False)
    fixed = ArtifactBuildService("unused", None, publisher=object())
    research = SimpleNamespace(
        store=object(),
        database="unused",
        trace=None,
        markdown_publisher=object(),
        _ref=None,
        _root_event_id=None,
        registration_policy=lambda: ResearchPolicy(synthesis_prompt="fixture research"),
    )
    harness, _ = compose_research(research, html_available=True)
    assert fixed.capabilities.bindings == harness.bindings
    assert fixed.capabilities.facts.registry.get("html", 1) == harness.facts.registry.get("html", 1)
    assert research.capabilities is harness
    unavailable = ArtifactBuildService("unused", None)
    assert unavailable.capabilities.discover("chat") == []
