from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from agent_execution.chat_run_input import ChatRunInputLoader
from agent_workflows.contracts import AGENT_SCHEMA_VERSION, AgentRunInput, RunContext, RunSource
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.config import TemporalConfig
from orchestration.run_lifecycle_activities import (
    inject_agent_run_loader,
    load_agent_run_input_activity,
)
from orchestration.run_lifecycle_contracts import RunLifecycleInput
from orchestration.web_dispatcher import TemporalClientAdapter


@pytest.mark.asyncio
async def test_dispatch_always_starts_canonical_workflow_and_rejects_legacy_owner():
    class Client:
        async def start_workflow(self, function, request, **options):
            assert function == AgentLifecycleWorkflow.run
            assert options["execution_timeout"] is None
            raise WorkflowAlreadyStartedError(options["id"], "WebRunWorkflow", run_id="old")

    with pytest.raises(RuntimeError, match="another Workflow"):
        await TemporalClientAdapter(Client()).start_web_run("run", RunLifecycleInput(1, "run"))


@pytest.mark.asyncio
async def test_lifecycle_loader_accepts_source_owned_non_chat_input():
    expected = AgentRunInput(
        schema_version=AGENT_SCHEMA_VERSION,
        run_id="run",
        account_id="account",
        source=RunSource("scheduled_task", "task"),
        context=RunContext(context_ref="ctx"),
        strategy="react",
    )
    inject_agent_run_loader(SimpleNamespace(load=lambda run_id: expected))
    assert await load_agent_run_input_activity(RunLifecycleInput(1, "run")) == expected


def test_chat_loader_rejects_missing_conversation_instead_of_stringifying_none():
    loader = ChatRunInputLoader(
        SimpleNamespace(
            run_identity=lambda run_id: {
                "status": "running",
                "run_kind": "chat",
                "conversation_id": None,
            }
        )
    )
    with pytest.raises(Exception, match="Chat source context unavailable"):
        loader.load("run")


def test_production_cutover_has_no_legacy_switch_or_host():
    assert "durable_agent_enabled" not in {field.name for field in fields(TemporalConfig)}
    root = Path(__file__).parents[1]
    source = (root / "src/orchestration/worker.py").read_text()
    composition = source[
        source.index("def compose_web_workers(") : source.index("def _build_web_background_tasks(")
    ]
    for legacy in (
        "WebExecutionHost",
        "DefaultBrainActionLoop",
        "execute_agent_activity",
        "durable_agent_enabled",
    ):
        assert legacy not in composition
    for file in (
        "src/orchestration/web_dispatcher.py",
        "src/web_api/config.py",
        ".env.example",
        "docker-compose.yaml",
    ):
        assert "DURABLE_AGENT_ENABLED" not in (root / file).read_text()


@pytest.mark.asyncio
async def test_dispatch_recovers_running_run_with_missing_start_record():
    from uuid import uuid4

    from orchestration.web_dispatcher import StartDecision, TemporalOutboxDispatcher

    run_id = uuid4()
    records = []
    store = SimpleNamespace(
        prepare_start=lambda value: StartDecision(value, "stable-workflow", True),
        still_queued=lambda value: False,
        record_started=lambda value, temporal_id: records.append((value, temporal_id)),
        needs_cancel=lambda value: False,
    )

    class Temporal:
        async def start_web_run(self, workflow_id, request):
            assert workflow_id == "stable-workflow"
            return "existing-temporal-run"

    assert await TemporalOutboxDispatcher(store, Temporal()).dispatch_start(run_id)
    assert records == [(run_id, "existing-temporal-run")]


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_failed", [False, True])
async def test_terminal_cancel_wins_over_late_agent_outcome(monkeypatch, agent_failed):
    import asyncio
    from unittest.mock import Mock

    from temporalio import workflow

    calls = []
    async def execute(name, request, **kwargs):
        calls.append(name)
        if name == "prepare_run_activity":
            return {"run_id": "run", "status": "running"}
        if name == "load_agent_run_input_activity":
            return SimpleNamespace(strategy="react")
        return {"run_id": "run", "status": "cancelled"}
    async def child(*args, **kwargs):
        if agent_failed:
            from temporalio.exceptions import ActivityError
            raise ActivityError("model failed", scheduled_event_id=1, started_event_id=2,
                                identity="worker", activity_type="model", activity_id="a", retry_state=None)
        return SimpleNamespace(result_ref="result")
    monkeypatch.setattr(workflow, "execute_activity", execute)
    monkeypatch.setattr(workflow, "execute_child_workflow", child)
    monkeypatch.setattr(workflow, "logger", Mock())
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(workflow_id="workflow", run_id="execution"))
    with pytest.raises(asyncio.CancelledError):
        await AgentLifecycleWorkflow().run(RunLifecycleInput(1, "run"))
    if agent_failed:
        assert calls[-1] == "finalize_failed_activity"
    else:
        assert calls[-2:] == ["finalize_agent_result_activity", "finalize_cancelled_activity"]
