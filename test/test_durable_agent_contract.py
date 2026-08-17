from __future__ import annotations

import inspect
from pathlib import Path

from agent_activities.runtime import DurableAgentActivities
from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.agent_step import AgentStepWorkflow
from agent_workflows.contracts import (
    AGENT_STRATEGY_PLAN,
    AGENT_STRATEGY_REACT,
    CompactToolCall,
    strategy_for_profile,
)
from agent_workflows.plan_execute import PlanAndExecuteWorkflow
from agent_workflows.react import ReactAgentWorkflow
from orchestration.artifact_workflow import ArtifactBuildWorkflow
from orchestration.durable_web_workflow import DurableWebRunWorkflow
from orchestration.web_workers import build_web_temporal_workers
from orchestration.web_workflow import WebRunWorkflow, WebRunWorkflowInput
from web_api.models import SendMessageRequest


def test_strategy_names_are_centralized_and_profiles_are_stable():
    assert strategy_for_profile("web_chat") == AGENT_STRATEGY_REACT
    assert strategy_for_profile("react") == AGENT_STRATEGY_REACT
    assert strategy_for_profile("web_plan") == AGENT_STRATEGY_PLAN
    assert strategy_for_profile("plan_and_execute") == AGENT_STRATEGY_PLAN
    assert strategy_for_profile("unknown") == "unknown"


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


def test_plan_evaluation_parser_supports_replan_and_safe_fallback():
    assert DurableAgentActivities._parse_evaluation(
        '{"decision":"replan","reason":"new evidence"}', "continue"
    ) == ("replan", "new evidence")
    assert DurableAgentActivities._parse_evaluation("bad", "complete") == (
        "complete",
        "evaluation_contract_fallback",
    )


def test_durable_worker_definitions_remain_registered_for_rollback(monkeypatch):
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
        WebRunWorkflow,
        DurableWebRunWorkflow,
        ArtifactBuildWorkflow,
    ]
    assert made[1]["workflows"] == [
        AgentRunWorkflow,
        ReactAgentWorkflow,
        PlanAndExecuteWorkflow,
        AgentStepWorkflow,
    ]


def test_legacy_workflow_input_contract_did_not_change():
    assert list(WebRunWorkflowInput.__dataclass_fields__) == [
        "schema_version",
        "run_id",
    ]


def test_lease_waiting_is_durable_and_cancellable_in_workflow_history():
    source = inspect.getsource(DurableWebRunWorkflow.run)
    assert "workflow.sleep" in source
    assert "execution_lease_conflict" not in source
    assert "durable_web_workflow_waiting_for_lease" in source


def test_hardening_migration_extends_operation_states_without_rewriting_014():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "persistence/migrations/015_durable_agent_hardening.sql").read_text()
    assert "intent_recorded" in migration
    assert "uncertain" in migration
    assert "015_durable_agent_hardening.sql" not in (
        root / "persistence/migrations/014_durable_agent_control_plane.sql"
    ).read_text()
