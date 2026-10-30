"""All trusted entry adapters share discovery, definitions and bindings."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_activities.composition import compose_capabilities
from research_activities.composition import compose_research
from web_artifacts.build import ArtifactBuildService


def test_catalog_derives_binding_worker_and_policy_names(monkeypatch):
    from temporalio import workflow

    from agent_activities import composition
    from application.tool_policy import harness_tool_names

    registrations = composition.capability_registrations()
    # A new contract uses the same composition path, without editing the Harness,
    # policy whitelist or Worker list. Reusing an implementation registers it once.
    extra = replace(registrations[1], definition=replace(
        registrations[1].definition, key="test.extra", contract_version=2,
    ))
    monkeypatch.setattr(composition, "capability_registrations", lambda: (*registrations, extra))
    catalog = composition.compose_capabilities(None, services=frozenset())
    binding = catalog.bindings["capability__test_dextra__v2"]
    assert (binding.key, binding.contract_version) == ("test.extra", 2)
    assert binding.workflow_name == workflow._Definition.must_from_class(extra.workflow).name
    assert binding.tool_name in harness_tool_names()
    assert composition.capability_workflows().count(extra.workflow) == 1
    assert binding.tool_name not in {t['function']['name'] for t in catalog.discover('work')}


def test_missing_isolated_binding_is_rejected_instead_of_silently_hidden():
    from agent_activities.capabilities import CapabilityActivities
    from run_domain.capabilities import CapabilityFacts
    from run_domain.capability_contracts import CapabilityRegistry
    from web_artifacts.capability import html_definition

    with pytest.raises(ValueError, match="binding"):
        CapabilityActivities(None, CapabilityFacts(CapabilityRegistry((html_definition(),))))


def test_catalog_adapters_preserve_parent_and_fixed_contracts():
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError

    catalog = compose_capabilities(None, services=frozenset({'html'}))
    binding = catalog.bindings['capability__html__v1']
    definition = catalog.facts.registry.get('html', 1)
    Draft202012Validator(definition['input_schema']).validate({'text': 'source'})
    with pytest.raises(ValidationError):
        Draft202012Validator(binding.input_schema(definition)).validate({'text': 'source'})
    Draft202012Validator(binding.input_schema(definition, model_facing=True)).validate({
        'text': 'source', 'source_ref': None, 'previous_ref': None, 'instruction': 'Keep controls',
    })


def test_capability_admission_is_owned_by_binding():
    catalog = compose_capabilities(None, services=frozenset())
    binding = catalog.bindings['capability__text__v1']
    run = {'input_snapshot': {'requirement': {'deliverables': {'items': [{'format': 'docx'}]}}}}
    with pytest.raises(ValueError, match='format'):
        binding.admit(None, run, {'title': 'Title', 'body': 'Body'})
    binding.admit(None, run, {'title': 'Title', 'body': 'Body', 'format': 'docx'})


def test_fixed_and_harness_child_inputs_use_binding_and_frozen_limits():
    from copy import deepcopy

    catalog = compose_capabilities(None, services=frozenset())
    frozen = deepcopy(catalog.facts.registry.get('research', 1))
    frozen['limits']['max_duration_seconds'] = 90
    frozen['retry_policy']['max_attempts'] = 2
    invocation = {
        'account_id': 'account', 'run_id': 'run', 'child_execution_id': 'child',
        'invocation_id': 'invocation', 'capability_key': 'research', 'contract_version': 1,
        'descriptor': {'definition': frozen},
    }
    child = catalog.child_input(invocation)
    assert child.workflow_name == catalog.binding_for('research', 1).workflow_name
    assert (child.max_duration_seconds, child.max_attempts) == (90, 2)
    assert child.operation_id == 'invocation:produce'
    from research_activities.parent import ResearchParentActivities

    parent = object.__new__(ResearchParentActivities)
    parent.capabilities = catalog
    assert parent._child(invocation) == child


def test_worker_activities_derive_from_trusted_implementations_and_require_all_services():
    from temporalio import activity

    from agent_activities.composition import capability_worker_activities
    from agent_activities.text_result import TextActivities
    from agent_workflows.contracts import AGENT_TASK_QUEUE
    from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE
    from research_activities import ResearchActivities
    from web_artifacts.local import HtmlActivities

    # Binding methods does not perform I/O; real dependencies are supplied at startup.
    implementations = [object.__new__(cls) for cls in (ResearchActivities, HtmlActivities, TextActivities)]
    grouped = capability_worker_activities(*implementations)
    names = {queue: {activity._Definition.must_from_callable(fn).name for fn in functions}
             for queue, functions in grouped.items()}
    assert names[AGENT_TASK_QUEUE] == {'produce_html_activity', 'produce_text_activity'}
    assert names[WEB_LIFECYCLE_TASK_QUEUE] == {
        'prepare_research_activity', 'create_research_plan_activity',
        'discover_research_sources_activity', 'rank_research_sources_activity',
        'fetch_research_sources_activity', 'normalize_research_sources_activity',
        'extract_research_evidence_activity', 'assess_research_corroboration_activity',
        'analyze_research_gaps_activity', 'synthesize_research_report_activity',
        'verify_research_citations_activity', 'compare_previous_research_activity',
        'commit_research_result_activity',
    }
    with pytest.raises(ValueError, match='implementation'):
        capability_worker_activities(*implementations[:2])


async def test_catalog_workflows_only_call_activities_on_the_registered_queue(monkeypatch):
    import importlib

    from temporalio import activity, workflow

    from agent_activities.composition import capability_registrations, capability_worker_activities
    from agent_workflows.capability_contracts import CapabilityExecutionInput

    entries = capability_registrations()
    grouped = capability_worker_activities(*(object.__new__(e.activity_type) for e in entries))
    available = {(queue, activity._Definition.must_from_callable(fn).name)
                 for queue, functions in grouped.items() for fn in functions}
    called = []

    async def execute(name, request, **options):
        called.append((options['task_queue'], name))
        return {'sufficient': True}

    monkeypatch.setattr(workflow, 'execute_activity', execute)
    monkeypatch.setattr(workflow, 'cancellation_reason', lambda: None)
    for entry in entries:
        module = importlib.import_module(entry.workflow.__module__)
        if hasattr(module, 'execute_segment'):
            monkeypatch.setattr(module, 'execute_segment', execute)
        await entry.workflow().run(CapabilityExecutionInput(
            'account', 'run', 'execution', 'invocation', entry.binding.workflow_name, 60, 3, 'operation',
        ))
    assert set(called) == available


def test_different_implementations_cannot_silently_share_an_activity_name(monkeypatch):
    from temporalio import activity

    from agent_activities import composition

    class ConflictingActivities:
        @activity.defn(name='produce_html_activity')
        async def conflicting(self):
            return 'different implementation'

    entries = composition.capability_registrations()
    conflict = replace(entries[1], definition=replace(entries[1].definition, key='test.conflict'),
                       activity_type=ConflictingActivities)
    monkeypatch.setattr(composition, 'capability_registrations', lambda: (*entries, conflict))
    with pytest.raises(ValueError, match='duplicate'):
        composition.capability_worker_activities(
            *(object.__new__(e.activity_type) for e in entries), ConflictingActivities(),
        )


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
    )
    harness, _ = compose_research(research, html_available=True)
    assert fixed.capabilities.bindings == harness.bindings
    assert fixed.capabilities.facts.registry.get("html", 1) == harness.facts.registry.get("html", 1)
    assert research.capabilities is harness
    unavailable = ArtifactBuildService("unused", None)
    assert unavailable.capabilities.discover("chat") == []
