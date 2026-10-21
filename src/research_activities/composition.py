"""Shared trusted registration for fixed Research and the generic Harness."""

from agent_activities.capabilities import CapabilityActivities, CapabilityBinding
from research_domain.capability import research_definition
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import CapabilityRegistry

from .parent import ResearchParentActivities


def compose_research(research, store=None):
    capabilities = CapabilityActivities(
        store or research.store,
        CapabilityFacts(
            CapabilityRegistry((research_definition(),)),
            services=frozenset({"research"} if research.markdown_publisher is not None else set()),
        ),
        (CapabilityBinding("research", 1, "ResearchCapabilityWorkflow"),),
    )
    research.capabilities = capabilities
    return capabilities, ResearchParentActivities(research, capabilities)
