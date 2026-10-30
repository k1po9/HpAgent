"""One trusted capability catalog for the Harness, fixed adapters and Worker.

This is static composition, never a model-controlled plugin loader. Service
availability, role assignment and resource authorization remain independent.
Keep this module out of Workflow imports.
"""

from dataclasses import dataclass, field
from typing import Any

from temporalio import activity, workflow

from agent_activities.capabilities import CapabilityActivities, CapabilityBinding
from agent_activities.text_result import (
    TextActivities,
    admit_text,
    compatible_text_definition,
    text_definition,
)
from agent_workflows.contracts import AGENT_TASK_QUEUE
from agent_workflows.html import HtmlCapabilityWorkflow
from agent_workflows.text import TextCapabilityWorkflow
from orchestration.research_workflow import ResearchCapabilityWorkflow
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE
from research_activities import ResearchActivities
from research_activities.admission import admit_research
from research_domain.capability import research_definition
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import CapabilityDefinition, CapabilityRegistry
from web_artifacts.capability import html_definition, html_parent_input_schema, html_scope
from web_artifacts.local import HtmlActivities


@dataclass(frozen=True)
class CapabilityRegistration:
    """Composition entry linking a versioned contract to its trusted adapter.

    Adapter options are the existing CapabilityBinding hooks. No execution
    authority or implementation names can come from configuration or model input.
    """

    definition: CapabilityDefinition
    workflow: type
    activity_type: type
    activity_queue: str
    adapter: dict[str, Any] = field(default_factory=dict)

    @property
    def binding(self) -> CapabilityBinding:
        return CapabilityBinding(
            self.definition.key,
            self.definition.contract_version,
            workflow._Definition.must_from_class(self.workflow).name,
            **self.adapter,
        )


def capability_registrations() -> tuple[CapabilityRegistration, ...]:
    return (
        CapabilityRegistration(research_definition(), ResearchCapabilityWorkflow,
                               ResearchActivities, WEB_LIFECYCLE_TASK_QUEUE, {
            'admit': admit_research,
        }),
        CapabilityRegistration(html_definition(), HtmlCapabilityWorkflow,
                               HtmlActivities, AGENT_TASK_QUEUE, {
            'scope': html_scope, 'input_schema': html_parent_input_schema,
            'description_suffix': ' Include every declared input field, using null for text/source_ref/previous_ref when not applicable.',
        }),
        CapabilityRegistration(text_definition(), TextCapabilityWorkflow,
                               TextActivities, AGENT_TASK_QUEUE, {
            'admit': admit_text, 'compatible_definition': compatible_text_definition,
        }),
    )


def capability_workflows() -> list[type]:
    """Register each implementation once, including unavailable definitions.

    Availability must not prevent old frozen invocations from being replayed.
    """
    return list(dict.fromkeys(entry.workflow for entry in capability_registrations()))


def capability_worker_activities(*implementations) -> dict[str, list[Any]]:
    """Bind decorated Activities of explicitly injected, trusted implementations.

    Queue placement is a composition decision, not inferred from service
    availability. Missing/ambiguous implementations fail before Worker startup.
    """
    grouped: dict[str, list[Any]] = {AGENT_TASK_QUEUE: [], WEB_LIFECYCLE_TASK_QUEUE: []}
    seen = {}
    for entry in capability_registrations():
        if entry.activity_queue not in grouped:
            raise ValueError('unsupported capability Activity queue')
        matches = [impl for impl in implementations if isinstance(impl, entry.activity_type)]
        if len(matches) != 1:
            raise ValueError(f'capability requires one implementation: {entry.definition.key}')
        found = False
        for name in dir(entry.activity_type):
            fn = getattr(matches[0], name)
            definition = activity._Definition.from_callable(fn)
            if definition is not None:
                found = True
                identity = (entry.activity_queue, definition.name)
                if identity in seen:
                    if seen[identity] != fn:
                        raise ValueError(f'duplicate capability Activity: {definition.name}')
                    continue
                seen[identity] = fn
                grouped[entry.activity_queue].append(fn)
        if not found:
            raise ValueError(f'capability implementation has no Activities: {entry.definition.key}')
    return grouped


def compose_capabilities(store, *, services: frozenset[str]) -> CapabilityActivities:
    entries = capability_registrations()
    return CapabilityActivities(
        store,
        CapabilityFacts(
            CapabilityRegistry(tuple(entry.definition for entry in entries)), services=services
        ),
        tuple(entry.binding for entry in entries),
    )
