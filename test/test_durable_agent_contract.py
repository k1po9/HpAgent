from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from agent_activities.runtime import DurableAgentActivities
from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.agent_step import AgentStepWorkflow
from agent_workflows.contracts import (
    AGENT_STRATEGY_PLAN,
    AGENT_STRATEGY_REACT,
    CompactToolCall,
)
from agent_workflows.plan_execute import PlanAndExecuteWorkflow
from agent_workflows.react import ReactAgentWorkflow
from agent_workflows.tool_execution import ToolExecutionWorkflow
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.artifact_workflow import ArtifactBuildWorkflow
from orchestration.document_workflow import NormalizeDocumentWorkflow
from orchestration.research_workflow import ResearchReportWorkflow, ResearchTaskScheduleWorkflow
from orchestration.run_lifecycle_contracts import RunLifecycleInput as WebRunWorkflowInput
from orchestration.web_workers import build_web_temporal_workers
from web_api.models import SendMessageRequest


def test_strategy_names_are_explicit_and_surface_neutral():
    assert AGENT_STRATEGY_REACT == "react"
    assert AGENT_STRATEGY_PLAN == "plan_and_execute"


def test_web_api_defaults_to_react_and_accepts_plan():
    assert SendMessageRequest(content="hello").agent_strategy == "react"
    assert (
        SendMessageRequest(
            content="hello", agent_strategy="plan_and_execute"
        ).agent_strategy
        == "plan_and_execute"
    )


def test_plan_parser_falls_back_to_original_objective():
    assert DurableAgentActivities._parse_plan(
        '{"steps":[{"title":"A","objective":"do A"}]}'
    ) == [("A", "do A")]
    assert DurableAgentActivities._parse_plan("not json", "original") == [
        ("完成目标", "original")
    ]


def test_tool_call_history_contract_uses_argument_reference():
    call = CompactToolCall("call-1", "fs_read", "agent-decision:op#call-1")
    assert call.arguments_ref.endswith("#call-1")
    assert not hasattr(call, "arguments")


def test_react_and_plan_step_share_tool_execution_child_workflow():
    for workflow_type in (ReactAgentWorkflow, AgentStepWorkflow):
        source = inspect.getsource(workflow_type.run)
        assert "ToolExecutionWorkflow.run" in source
        assert '"tool_execution_activity"' not in source


def test_plan_evaluation_parser_supports_replan_and_safe_fallback():
    assert DurableAgentActivities._parse_evaluation(
        '{"decision":"replan","reason":"new evidence"}', "continue"
    ) == ("replan", "new evidence")
    assert DurableAgentActivities._parse_evaluation("bad", "complete") == (
        "complete",
        "evaluation_contract_fallback",
    )


def test_canonical_registry_excludes_legacy_execution(monkeypatch):
    made: list[dict] = []

    class FakeWorker:
        def __init__(self, _client, **kwargs):
            made.append(kwargs)

    monkeypatch.setattr("orchestration.web_workers.Worker", FakeWorker)
    build_web_temporal_workers(
        object(),
        lifecycle_activities=[],
        agent_activities=[],
    )
    assert made[0]["workflows"] == [
        AgentLifecycleWorkflow,
        ResearchReportWorkflow,
        ResearchTaskScheduleWorkflow,
        ArtifactBuildWorkflow,
        NormalizeDocumentWorkflow,
    ]
    assert made[1]["workflows"] == [
        AgentRunWorkflow,
        ReactAgentWorkflow,
        PlanAndExecuteWorkflow,
        AgentStepWorkflow,
        ToolExecutionWorkflow,
    ]


def test_legacy_workflow_input_contract_did_not_change():
    assert list(WebRunWorkflowInput.__dataclass_fields__) == [
        "schema_version",
        "run_id",
    ]


def test_lifecycle_does_not_acquire_a_lease_for_the_entire_agent_child():
    source = inspect.getsource(AgentLifecycleWorkflow.run)
    assert '"load_agent_run_input_activity"' in source
    assert '"acquire_execution_lease_activity"' not in source
    assert '"release_execution_lease_activity"' not in source


def test_hardening_migration_extends_operation_states_without_rewriting_014():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "persistence/migrations/015_durable_agent_hardening.sql").read_text()
    assert "intent_recorded" in migration
    assert "uncertain" in migration
    assert "015_durable_agent_hardening.sql" not in (
        root / "persistence/migrations/014_durable_agent_control_plane.sql"
    ).read_text()


@pytest.mark.asyncio
async def test_already_cancelled_run_finalizes_without_waiting_for_a_signal(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock

    from temporalio import workflow

    calls = []
    async def execute(name, request, **kwargs):
        calls.append(name)
        return {"run_id": request.run_id, "status": "cancelled"}

    monkeypatch.setattr(workflow, "execute_activity", execute)
    monkeypatch.setattr(workflow, "logger", Mock())
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(workflow_id="workflow", run_id="execution"))
    with pytest.raises(asyncio.CancelledError):
        await AgentLifecycleWorkflow().run(WebRunWorkflowInput(1, "run"))
    assert calls == ["prepare_run_activity", "finalize_cancelled_activity"]


@pytest.mark.asyncio
async def test_durable_run_timeout_is_independent_of_legacy_execution_limit():
    from types import SimpleNamespace

    from orchestration.web_dispatcher import TemporalClientAdapter

    class Client:
        async def start_workflow(self, *args, **kwargs):
            assert kwargs["execution_timeout"] is None
            return SimpleNamespace(result_run_id="temporal-run")

    assert await TemporalClientAdapter(Client()).start_web_run(
        "workflow", WebRunWorkflowInput(1, "run"),
    ) == "temporal-run"


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum_attempts", [1, 3, "default"])
async def test_segment_capability_retry_is_bounded_and_releases_between_attempts(monkeypatch, maximum_attempts):
    from dataclasses import dataclass
    from uuid import uuid4

    from temporalio.common import RetryPolicy
    from temporalio.exceptions import ActivityError

    from agent_workflows import segments
    from agent_workflows.lifecycle_contracts import SegmentLease

    @dataclass
    class Request:
        run_id: str = "run"
        account_id: str = "account"
        lease_token: int = 0
        execution_attempt: int = 1

    events = []
    tokens = []
    segment_ids = []

    async def control(name, request, result_type=None):
        if name == "acquire_agent_segment_activity":
            events.append("acquire")
            segment_ids.append(request.segment_id)
            return SegmentLease(True, len(segment_ids))
        assert name == "release_agent_segment_activity"
        events.append("release")

    failure = ActivityError("retryable", scheduled_event_id=1, started_event_id=2,
                            identity="worker", activity_type="capability", activity_id="activity", retry_state=None)

    async def execute(name, request, **options):
        assert options["retry_policy"].maximum_attempts == 1
        events.append("attempt")
        tokens.append((request.execution_attempt, request.lease_token))
        raise failure

    async def sleep(delay):
        assert events[-1] == "release"
        events.append("timer")

    monkeypatch.setattr(segments, "_control", control)
    monkeypatch.setattr(segments.workflow, "uuid4", uuid4)
    monkeypatch.setattr(segments.workflow, "execute_activity", execute)
    monkeypatch.setattr(segments.workflow, "sleep", sleep)
    options = {} if maximum_attempts == "default" else {"retry_policy": RetryPolicy(maximum_attempts=maximum_attempts)}
    with pytest.raises(ActivityError) as raised:
        await segments.execute_segment("capability", Request(), **options)
    assert raised.value is failure
    attempts = 3 if maximum_attempts == "default" else maximum_attempts
    assert tokens == [(attempt, attempt) for attempt in range(1, attempts + 1)]
    assert len(set(segment_ids)) == attempts
    assert events == ["acquire", "attempt", "release", "timer"] * (attempts - 1) + ["acquire", "attempt", "release"]


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum_attempts", [0, -1, None])
async def test_segment_rejects_unbounded_retry_before_acquiring(monkeypatch, maximum_attempts):
    from temporalio.common import RetryPolicy

    from agent_workflows import segments

    def unexpected_uuid():
        pytest.fail("invalid retry policy must be rejected before allocating a segment")

    monkeypatch.setattr(segments.workflow, "uuid4", unexpected_uuid)
    with pytest.raises(ValueError, match="^execute_segment requires a bounded retry policy$"):
        await segments.execute_segment("capability", object(), retry_policy=RetryPolicy(maximum_attempts=maximum_attempts))


def test_control_plane_retry_remains_unlimited():
    from agent_workflows.segments import _CONTROL_RETRY

    assert _CONTROL_RETRY.maximum_attempts == 0
