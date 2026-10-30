"""All trusted entry adapters share discovery, definitions and bindings."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_activities.composition import compose_capabilities
from research_activities.composition import compose_research
from research_domain.policy import ResearchPolicy
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
    monkeypatch.setattr(composition, "capability_registrations", lambda **_: (*registrations, extra))
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
        'descriptor': {'definition': frozen, 'limits': frozen['limits']},
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
    monkeypatch.setattr(composition, 'capability_registrations', lambda **_: (*entries, conflict))
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


def test_research_policy_uses_catalog_binding_and_retains_frozen_compatibility():
    from copy import deepcopy

    from research_domain.capability import research_definition

    policy = ResearchPolicy(max_sources=7, synthesis_prompt='frozen research prompt')
    catalog = compose_capabilities(None, services=frozenset({'research'}), research_policy=policy)
    current = catalog.facts.registry.get('research', 1)
    binding = catalog.binding_for('research', 1)
    assert current == research_definition(policy).to_dict()
    assert binding.compatible_definition(research_definition().to_dict(), current)
    previous = research_definition(replace(policy, max_sources=3)).to_dict()
    assert binding.compatible_definition(previous, current)
    corrupted = deepcopy(previous)
    corrupted['retry_policy']['max_attempts'] = 9
    assert not binding.compatible_definition(corrupted, current)


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


# Derive cases from the catalog so each new adapter inherits the contract gate.
from agent_activities.composition import capability_registrations


@pytest.mark.parametrize('entry', capability_registrations(), ids=lambda e: e.definition.key)
async def test_every_capability_preserves_execution_identity_and_bounded_activity_policy(monkeypatch, entry):
    from support.capability_contract import WorkflowProbe
    from temporalio import activity

    from agent_workflows.capability_contracts import CapabilityExecutionInput

    probe = WorkflowProbe()
    probe.install(monkeypatch, entry.workflow)
    request = CapabilityExecutionInput(
        'account', 'run', 'execution', 'invocation', entry.binding.workflow_name, 60, 3, 'operation',
    )
    await entry.workflow().run(request)
    registered = {d.name for name in dir(entry.activity_type)
                  if (d := activity._Definition.from_callable(getattr(entry.activity_type, name))) is not None}
    assert probe.calls
    for name, current, options in probe.calls:
        assert name in registered
        assert (current.account_id, current.run_id, current.execution_id, current.invocation_id) == (
            request.account_id, request.run_id, request.execution_id, request.invocation_id,
        )
        assert options['task_queue'] == entry.activity_queue
        assert options['start_to_close_timeout'].total_seconds() > 0
        assert options['retry_policy'].maximum_attempts > 0


@pytest.mark.parametrize('entry', capability_registrations(), ids=lambda e: e.definition.key)
@pytest.mark.parametrize('cancelled', [False, True], ids=['failure', 'cancellation'])
async def test_every_capability_propagates_failure_without_committing_success(monkeypatch, entry, cancelled):
    import asyncio

    from support.capability_contract import WorkflowProbe

    from agent_workflows.capability_contracts import CapabilityExecutionInput

    failure = asyncio.CancelledError() if cancelled else RuntimeError('injected Activity failure')
    probe = WorkflowProbe(failure=failure)
    probe.install(monkeypatch, entry.workflow)
    request = CapabilityExecutionInput(
        'account', 'run', 'execution', 'invocation', entry.binding.workflow_name, 60, 3, 'operation',
    )
    with pytest.raises(type(failure)):
        await entry.workflow().run(request)
    assert len(probe.calls) == 1  # no later publication/verification after failed first stage


@pytest.mark.parametrize('entry', capability_registrations(), ids=lambda e: e.definition.key)
def test_every_catalog_binding_uses_frozen_bounds_and_valid_parent_schemas(entry):
    from copy import deepcopy

    from jsonschema import Draft202012Validator

    catalog = compose_capabilities(None, services=frozenset())
    frozen = deepcopy(entry.definition.to_dict())
    frozen['retry_policy']['max_attempts'] = 1
    child = catalog.child_input({
        'account_id': 'account', 'run_id': 'run', 'child_execution_id': 'child',
        'invocation_id': 'invocation', 'capability_key': entry.definition.key,
        'contract_version': entry.definition.contract_version, 'descriptor': {
            'definition': frozen, 'limits': {**frozen['limits'], 'max_duration_seconds': 7},
        },
    })
    assert (child.max_duration_seconds, child.max_attempts) == (7, 1)
    for model_facing in (False, True):
        Draft202012Validator.check_schema(entry.binding.input_schema(frozen, model_facing=model_facing))


@pytest.mark.parametrize('outcome', ['failed', 'cancelled', 'succeeded'])
async def test_common_invocation_closes_exact_child_and_only_observes_after_success(monkeypatch, outcome):
    import asyncio

    from temporalio import workflow

    from agent_workflows import capability
    from agent_workflows.capability_contracts import CapabilityExecutionInput, CapabilityPrepared
    from agent_workflows.contracts import CompactToolCall, RunContext, RunSource, ToolExecutionInput

    tool = ToolExecutionInput(
        2, 'run', 'account', RunSource('work', 'work'), RunContext(), 'react', 'transcript',
        1, 0, 'caller-operation', 0, CompactToolCall('call', 'tool', 'arguments'), execution_id='parent',
    )
    child = CapabilityExecutionInput('account', 'run', 'child', 'invocation', 'TrustedWorkflow', 60, 2, 'produce')
    closed, phases = [], []
    expected = object()

    async def execute(name, request, **options):
        phases.append(name)
        if name == 'prepare_capability_activity':
            return CapabilityPrepared(child=child)
        assert name == 'observe_capability_activity'
        assert request.call.execution_id == 'parent'
        return expected

    async def start(name, request, **options):
        assert name == 'TrustedWorkflow' and request == child
        assert options['cancellation_type'] == workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED

        async def result():
            if outcome == 'failed':
                raise RuntimeError('child failed')
            if outcome == 'cancelled':
                raise asyncio.CancelledError
        return asyncio.create_task(result())

    async def close(name, request, **options):
        assert name == 'close_capability_activity'
        closed.append(request)

    monkeypatch.setattr(capability, 'execute_segment', execute)
    monkeypatch.setattr(workflow, 'start_child_workflow', start)
    monkeypatch.setattr(workflow, 'execute_activity', close)
    if outcome == 'succeeded':
        assert await capability.CapabilityInvocationWorkflow().run(tool) is expected
        assert phases[-1] == 'observe_capability_activity'
    else:
        with pytest.raises(asyncio.CancelledError if outcome == 'cancelled' else RuntimeError):
            await capability.CapabilityInvocationWorkflow().run(tool)
        assert phases == ['prepare_capability_activity']
    assert len(closed) == 1 and closed[0].child == child and closed[0].status == outcome


@pytest.mark.parametrize('entry', capability_registrations(), ids=lambda e: e.definition.key)
async def test_implementation_retry_does_not_exceed_frozen_child_bound(monkeypatch, entry):
    from support.capability_contract import WorkflowProbe

    from agent_workflows.capability_contracts import CapabilityExecutionInput

    probe = WorkflowProbe()
    probe.install(monkeypatch, entry.workflow)
    request = CapabilityExecutionInput('account', 'run', 'execution', 'invocation', entry.binding.workflow_name, 60, 1, 'operation')
    await entry.workflow().run(request)
    assert all(options['retry_policy'].maximum_attempts <= request.max_attempts for _, _, options in probe.calls)


async def test_research_legacy_child_preserves_stage_retry_options(monkeypatch):
    from support.capability_contract import WorkflowProbe

    from agent_workflows.capability_contracts import CapabilityExecutionInput
    from orchestration.research_workflow import _RESEARCH_RETRY, ResearchCapabilityWorkflow

    probe = WorkflowProbe()
    probe.install(monkeypatch, ResearchCapabilityWorkflow)
    child = CapabilityExecutionInput('account', 'run', 'execution', 'invocation',
                                     'ResearchCapabilityWorkflow', 3600, 4, 'produce')
    await ResearchCapabilityWorkflow().run(child)
    assert len(probe.calls) == 13
    assert all(options['retry_policy'] == _RESEARCH_RETRY for _, _, options in probe.calls)


@pytest.mark.parametrize('entry', capability_registrations(), ids=lambda e: e.definition.key)
def test_each_capability_discovery_requires_services_surface_and_role_assignment(entry):
    from agent_activities.capabilities import CapabilityActivities
    from application.tool_policy import ToolPolicy

    services = frozenset(entry.definition.required_services)
    catalog = compose_capabilities(None, services=services)
    for surface, role in (('chat', 'main'), ('work', 'worker')):
        names = {tool['function']['name'] for tool in catalog.discover(surface)}
        expected = surface in entry.definition.allowed_surfaces and catalog.tool_policy.allows(role, entry.binding.tool_name)
        assert (entry.binding.tool_name in names) is expected
        for missing in services:
            unavailable = compose_capabilities(None, services=services - {missing})
            assert entry.binding.tool_name not in {tool['function']['name'] for tool in unavailable.discover(surface)}
    configured = catalog.tool_policy.model_dump()
    for role in ('main', 'worker'):
        configured['roles'][role]['groups'] = ()
    denied = CapabilityActivities(None, catalog.facts, tuple(catalog.bindings.values()), policy=ToolPolicy.model_validate(configured))
    assert denied.discover('chat') == denied.discover('work') == []
