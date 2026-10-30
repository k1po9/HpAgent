"""One trusted capability catalog for the Harness and fixed entry adapters.

Service availability is explicit; registration never grants resource permission.
This module belongs to Activity composition and must not enter Workflow imports.
"""

from agent_activities.capabilities import CapabilityActivities, CapabilityBinding
from agent_activities.text_result import text_definition
from research_domain.capability import research_definition
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import CapabilityRegistry
from web_artifacts.capability import html_definition, html_scope


def compose_capabilities(store, *, services: frozenset[str], research_policy=None) -> CapabilityActivities:
    return CapabilityActivities(
        store,
        CapabilityFacts(
            CapabilityRegistry((research_definition(research_policy), html_definition(), text_definition())), services=services
        ),
        (
            CapabilityBinding("text", 1, "TextCapabilityWorkflow"),
            CapabilityBinding("research", 1, "ResearchCapabilityWorkflow"),
            CapabilityBinding("html", 1, "HtmlCapabilityWorkflow", html_scope),
        ),
    )
