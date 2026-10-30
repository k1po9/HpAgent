"""Shared trusted registration for fixed Research and the generic Harness."""

from agent_activities.composition import compose_capabilities

from .parent import ResearchParentActivities


def compose_research(research, store=None, *, html_available=False):
    services = frozenset(
        ({"research"} if research.markdown_publisher is not None else set())
        | ({"html", "text"} if html_available else set())
    )
    capabilities = compose_capabilities(store or research.store, services=services,
                                        research_policy=research.registration_policy())
    research.capabilities = capabilities
    return capabilities, ResearchParentActivities(research, capabilities)
