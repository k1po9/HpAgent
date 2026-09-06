from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError

from actions.runtime import ActionRuntime
from agent.protocol import ActionRequest, ActionResult
from agent_activities.runtime import DurableAgentActivities
from agent_activities.side_effects import UnsupportedToolSideEffectReconciler
from agent_activities.store import (
    LeaseConflict,
    StaleFencingToken,
    ToolOperationState,
    TranscriptVersionConflict,
)
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    CompactToolCall,
    PlanEvaluationInput,
    PlanningInput,
    ToolExecutionInput,
)
from orchestration import web_activities
from orchestration.durable_web_workflow import AcquireLeaseInput
from sandbox.tools.adapters.mcp import CachedTool, MCPToolManager, _build_langchain_tool
from web_domain.failures import is_failure_retryable


@pytest.fixture(autouse=True)
def direct_to_thread(monkeypatch):
    async def run(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("agent_activities.runtime.asyncio.to_thread", run)


class Events:
    async def progress(self, phase: str, summary: str) -> None:
        return None

    async def close(self) -> None:
        return None


class EventFactory:
    def for_run(self, run_id: str) -> Events:
        return Events()


class ResourcePrep:
    @asynccontextmanager
    async def lease_for_run(self, account_id, run_id, control):
        yield


class Store:
    def __init__(
        self,
        state: ToolOperationState,
        *,
        fence_on_validation: int | None = None,
        completion_error: Exception | None = None,
        load_error: BaseException | None = None,
    ):
        self.state = state
        self.fence_on_validation = fence_on_validation
        self.completion_error = completion_error
        self.load_error = load_error
        self.validations = 0
        self.uncertain: list[str] = []
        self.failed: list[str] = []
        self.completion: dict | None = None

    def begin_tool_operation(self, operation_id: str, run_id: str) -> ToolOperationState:
        return self.state

    def validate_and_renew_lease(self, account_id: str, run_id: str, token: int):
        self.validations += 1
        if self.validations == self.fence_on_validation:
            raise StaleFencingToken("taken over")

    def load_messages(self, transcript_id: str):
        if self.load_error is not None:
            raise self.load_error
        return [], 1

    def tool_call_arguments(self, arguments_ref: str, tool_call_id: str):
        return {"value": 1}

    def record_operation_intent(self, operation_id: str, intent: dict):
        self.state = ToolOperationState("intent_recorded", intent)

    def complete_operation_with_event(self, **kwargs):
        if self.completion_error is not None:
            raise self.completion_error
        self.completion = kwargs
        return 2

    def fail_operation(self, operation_id: str, code: str):
        self.failed.append(code)

    def mark_operation_uncertain(self, operation_id: str, code: str):
        self.uncertain.append(code)


class Actions:
    def __init__(
        self,
        side_effect_class: str,
        *,
        error: str | None = None,
        cancel: bool = False,
    ):
        self.classification = side_effect_class
        self.error = error
        self.cancel = cancel
        self.calls = 0

    def side_effect_class(self, session_id: str, tool_name: str) -> str:
        return self.classification

    def budget_reservation(self, session_id: str, tool_name: str) -> dict[str, int]:
        return {"tool_calls": 1, "bytes_scanned": 100}

    async def execute_request(self, request, **kwargs):
        self.calls += 1
        if self.cancel:
            raise asyncio.CancelledError
        return ActionResult(
            request=request,
            success=self.error is None,
            output="ok",
            metadata={
                "budget_usage": {"bytes_scanned": 17},
                "trace_metadata": {"count": 2},
            },
            error=self.error,
            raw={
                "success": self.error is None,
                "output": "ok",
                "error": self.error,
            },
        )

    def clear_execution(self, session_id: str, run_id: str) -> None:
        return None


class CrashAfterSideEffect:
    def hit(self, point: str) -> None:
        assert point == "tool_side_effect_succeeded_before_ack"
        raise RuntimeError("injected crash")


def request() -> ToolExecutionInput:
    run_id = str(uuid4())
    return ToolExecutionInput(
        AGENT_SCHEMA_VERSION,
        run_id,
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
        "react",
        f"transcript:{run_id}",
        1,
        1,
        f"{run_id}:tool:call-1",
        11,
        CompactToolCall("call-1", "write_tool", "decision:1#call-1"),
    )


def activities(store: Store, actions: Actions, **kwargs) -> DurableAgentActivities:
    return DurableAgentActivities(
        store=store,
        loader=None,
        brain=None,
        actions=actions,
        event_factory=EventFactory(),
        resource_prep=ResourcePrep(),
        lifecycle=None,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_tool_activity_reserves_and_settles_measured_usage_once():
    calls: list[tuple] = []

    class Budget:
        def reserve(self, run_id, operation_id, values):
            calls.append(("reserve", run_id, operation_id, values))

        def settle(self, run_id, operation_id, values, source):
            calls.append(("settle", run_id, operation_id, values, source))

    item = request()
    result = await activities(
        Store(ToolOperationState("started", None)),
        Actions("read_only"),
        run_budget=Budget(),
    ).tool_execution(item)

    assert result.operation_id == item.operation_id
    assert calls == [
        (
            "reserve", item.run_id, item.operation_id,
            {"tool_calls": 1, "bytes_scanned": 100},
        ),
        (
            "settle", item.run_id, item.operation_id,
            {"tool_calls": 1, "bytes_scanned": 17}, "measured",
        ),
    ]


@pytest.mark.asyncio
async def test_read_only_tool_failure_is_a_completed_failure_observation():
    item = request()
    store = Store(ToolOperationState("started", None))

    result = await activities(
        store, Actions("read_only", error="mock read error")
    ).tool_execution(item)

    assert result.tool_success is False
    assert result.display_summary == "Tool write_tool failed: mock read error"
    assert store.completion is not None
    event = store.completion["event_payload"]
    assert event["message"]["content"] == (
        '{"success": false, "error": "mock read error"}'
    )
    assert event["raw_result"]["error"] == "mock read error"
    assert store.completion["result_payload"]["tool_success"] is False


@pytest.mark.asyncio
async def test_file_tool_activity_projects_only_aggregate_trace_metadata():
    recorded: list[tuple] = []

    class TraceEvents(Events):
        async def trace_start(
            self, node_id, parent_id, name, node_type, metadata=None
        ):
            recorded.append(("start", name, metadata))

        async def trace_end(self, node_id, status, metadata=None):
            recorded.append(("end", status, metadata))

    class TraceFactory:
        def for_run(self, run_id):
            return TraceEvents()

    item = request()
    item = replace(
        item,
        tool_call=CompactToolCall(
            "call-1", "count_matches", "decision:1#call-1"
        ),
    )
    runtime = DurableAgentActivities(
        store=Store(ToolOperationState("started", None)),
        loader=None,
        brain=None,
        actions=Actions("read_only"),
        event_factory=TraceFactory(),
        resource_prep=ResourcePrep(),
        lifecycle=None,
    )

    await runtime.tool_execution(item)

    assert ("start", "FileCount", {}) in recorded
    assert (
        "end", "completed", {"scanned_bytes": 17, "returned_bytes": 0, "count": 2}
    ) in recorded


@pytest.mark.asyncio
async def test_plan_activity_logging_does_not_duplicate_correlation_fields():
    class PlanStore:
        def begin_operation(self, operation_id: str, run_id: str, kind: str):
            return None

        def validate_and_renew_lease(self, account_id: str, run_id: str, token: int):
            return None

        def load_messages(self, transcript_id: str):
            return [{"role": "user", "content": "read version"}], 1

        def complete_operation_with_event(self, **kwargs):
            return 2

        def complete_operation(self, operation_id: str, result_ref: str, payload: dict):
            return None

        def fail_operation(self, operation_id: str, code: str):
            raise AssertionError(f"unexpected failure: {operation_id} {code}")

    class PlanBrain:
        def __init__(self):
            self.calls = 0

        async def generate_final_decision(self, *, messages):
            self.calls += 1
            content = (
                '{"steps":[{"title":"read","objective":"read version"}]}'
                if self.calls == 1
                else '{"decision":"complete","reason":"done"}'
            )
            return SimpleNamespace(content=content)

    run_id = str(uuid4())
    identity = (str(uuid4()), str(uuid4()), str(uuid4()))
    durable = DurableAgentActivities(
        store=PlanStore(),
        loader=None,
        brain=PlanBrain(),
        actions=Actions("read_only"),
        event_factory=EventFactory(),
        resource_prep=ResourcePrep(),
        lifecycle=None,
    )
    planned = await durable.planning(
        PlanningInput(
            AGENT_SCHEMA_VERSION,
            run_id,
            *identity,
            f"transcript:{run_id}",
            1,
            f"{run_id}:plan:1:planner",
            1,
            f"plan:{run_id}",
            1,
        )
    )
    assert len(planned.steps) == 1
    evaluated = await durable.evaluate_plan(
        PlanEvaluationInput(
            AGENT_SCHEMA_VERSION,
            run_id,
            *identity,
            f"transcript:{run_id}",
            1,
            f"{run_id}:plan:1:step:step-1:evaluation",
            1,
            f"plan:{run_id}",
            1,
            "step-1",
            1,
            1,
        )
    )
    assert evaluated.decision == "complete"


@pytest.mark.asyncio
async def test_second_fencing_check_prevents_side_effect_after_local_lock_wait():
    store = Store(ToolOperationState("started", None), fence_on_validation=2)
    actions = Actions("external_write")
    with pytest.raises(ApplicationError) as failure:
        await activities(store, actions).tool_execution(request())
    assert failure.value.type == "stale_fencing_token"
    assert store.validations == 2
    assert actions.calls == 0


@pytest.mark.asyncio
async def test_non_idempotent_ack_gap_fails_closed_when_reconciliation_is_unsupported():
    req = request()
    intent = {
        "schema_version": 1,
        "tool_call_id": "call-1",
        "tool": "write_tool",
        "arguments_hash": "48208f9428d64634bd8e28ff345bf0eab60d53c18fa2fbdb0b9bc1e84df2b5f6",
        "side_effect_class": "non_idempotent_write",
        "fencing_token": 11,
    }
    store = Store(ToolOperationState("intent_recorded", intent))
    actions = Actions("external_write")
    with pytest.raises(ApplicationError) as failure:
        await activities(
            store,
            actions,
            reconciler=UnsupportedToolSideEffectReconciler(),
        ).tool_execution(req)
    assert failure.value.type == "tool_side_effect_uncertain"
    assert actions.calls == 0
    assert store.uncertain == ["tool_side_effect_uncertain"]


@pytest.mark.asyncio
async def test_fault_hook_marks_ack_gap_uncertain_after_exactly_one_side_effect():
    store = Store(ToolOperationState("started", None))
    actions = Actions("external_write")
    with pytest.raises(ApplicationError) as failure:
        await activities(
            store,
            actions,
            fault_injector=CrashAfterSideEffect(),
        ).tool_execution(request())
    assert failure.value.type == "tool_side_effect_uncertain"
    assert failure.value.non_retryable is True
    assert actions.calls == 1
    assert store.uncertain == ["tool_side_effect_uncertain"]


@pytest.mark.asyncio
async def test_idempotent_write_ack_gap_retry_reuses_budget_operation():
    store = Store(ToolOperationState("started", None))
    actions = Actions("idempotent_write")

    class Budget:
        settled: dict[str, dict[str, int]] = {}
        used_tool_calls = 0

        def reserve(self, run_id, operation_id, values):
            return None

        def settle(self, run_id, operation_id, values, source):
            previous = self.settled.get(operation_id)
            if previous is None:
                self.settled[operation_id] = dict(values)
                self.used_tool_calls += values["tool_calls"]
            else:
                assert previous == values

    budget = Budget()
    item = request()
    with pytest.raises(ApplicationError):
        await activities(
            store, actions, run_budget=budget,
            fault_injector=CrashAfterSideEffect(),
        ).tool_execution(item)
    result = await activities(
        store, actions, run_budget=budget
    ).tool_execution(item)

    assert result.operation_id == item.operation_id
    assert budget.used_tool_calls == 1


@pytest.mark.asyncio
async def test_non_idempotent_completion_failure_is_exposed_as_uncertain():
    store = Store(
        ToolOperationState("started", None),
        completion_error=RuntimeError("database write failed"),
    )
    actions = Actions("non_idempotent_write")
    with pytest.raises(ApplicationError) as failure:
        await activities(store, actions).tool_execution(request())
    assert failure.value.type == "tool_side_effect_uncertain"
    assert failure.value.non_retryable is True
    assert actions.calls == 1
    assert store.uncertain == ["tool_side_effect_uncertain"]


@pytest.mark.asyncio
async def test_non_idempotent_transcript_conflict_after_side_effect_is_uncertain():
    store = Store(
        ToolOperationState("started", None),
        completion_error=TranscriptVersionConflict("concurrent transcript update"),
    )
    actions = Actions("non_idempotent_write")
    with pytest.raises(ApplicationError) as failure:
        await activities(store, actions).tool_execution(request())
    assert failure.value.type == "tool_side_effect_uncertain"
    assert store.uncertain == ["tool_side_effect_uncertain"]
    assert store.failed == []


@pytest.mark.asyncio
async def test_redelivered_non_idempotent_intent_with_stale_fence_is_uncertain():
    req = request()
    intent = {
        "schema_version": 1,
        "tool_call_id": "call-1",
        "tool": "write_tool",
        "arguments_hash": "stable",
        "side_effect_class": "non_idempotent_write",
        "fencing_token": 11,
    }
    store = Store(
        ToolOperationState("intent_recorded", intent),
        fence_on_validation=1,
    )
    with pytest.raises(ApplicationError) as failure:
        await activities(store, Actions("non_idempotent_write")).tool_execution(req)
    assert failure.value.type == "tool_side_effect_uncertain"
    assert failure.value.non_retryable is True
    assert store.uncertain == ["tool_side_effect_uncertain"]
    assert store.failed == []


@pytest.mark.asyncio
async def test_redelivered_non_idempotent_intent_rejects_classification_drift():
    req = request()
    intent = {
        "schema_version": 1,
        "tool_call_id": "call-1",
        "tool": "write_tool",
        "arguments_hash": "48208f9428d64634bd8e28ff345bf0eab60d53c18fa2fbdb0b9bc1e84df2b5f6",
        "side_effect_class": "non_idempotent_write",
        "fencing_token": 11,
    }
    store = Store(ToolOperationState("intent_recorded", intent))
    actions = Actions("read_only")
    with pytest.raises(ApplicationError) as failure:
        await activities(store, actions).tool_execution(req)
    assert failure.value.type == "tool_side_effect_uncertain"
    assert actions.calls == 0
    assert store.uncertain == ["tool_side_effect_uncertain"]


@pytest.mark.asyncio
async def test_pre_side_effect_stale_fence_preserves_original_error():
    store = Store(ToolOperationState("started", None), fence_on_validation=1)
    actions = Actions("non_idempotent_write")
    with pytest.raises(ApplicationError) as failure:
        await activities(store, actions).tool_execution(request())
    assert failure.value.type == "stale_fencing_token"
    assert actions.calls == 0
    assert store.uncertain == []
    assert store.failed == ["stale_fencing_token"]


@pytest.mark.asyncio
async def test_non_idempotent_action_error_is_uncertain_and_never_completed():
    store = Store(ToolOperationState("started", None))
    actions = Actions("non_idempotent_write", error="provider timed out")
    with pytest.raises(ApplicationError) as failure:
        await activities(store, actions).tool_execution(request())
    assert failure.value.type == "tool_side_effect_uncertain"
    assert failure.value.non_retryable is True
    assert actions.calls == 1
    assert store.uncertain == ["tool_side_effect_uncertain"]


@pytest.mark.asyncio
async def test_cancellation_before_side_effect_window_fails_operation_and_propagates():
    store = Store(
        ToolOperationState("started", None),
        load_error=asyncio.CancelledError(),
    )
    actions = Actions("non_idempotent_write")

    with pytest.raises(asyncio.CancelledError):
        await activities(store, actions).tool_execution(request())

    assert actions.calls == 0
    assert store.failed == ["activity_cancelled"]
    assert store.uncertain == []


@pytest.mark.asyncio
async def test_cancellation_after_non_idempotent_window_is_uncertain_and_propagates():
    store = Store(ToolOperationState("started", None))
    actions = Actions("non_idempotent_write", cancel=True)

    with pytest.raises(asyncio.CancelledError):
        await activities(store, actions).tool_execution(request())

    assert actions.calls == 1
    assert store.failed == []
    assert store.uncertain == ["tool_side_effect_uncertain"]


def test_uncertain_side_effect_failures_are_not_run_retryable():
    assert is_failure_retryable("tool_side_effect_uncertain") is False
    assert is_failure_retryable("side_effect_reconciliation_failed") is False
    assert is_failure_retryable("model_unavailable") is True


@pytest.mark.asyncio
async def test_busy_execution_lease_returns_wait_state_and_progress(monkeypatch):
    phases: list[str] = []

    class BusyStore:
        def run_identity(self, run_id: str):
            return {
                "account_id": str(uuid4()),
                "conversation_id": str(uuid4()),
                "session_id": str(uuid4()),
                "trigger_message_id": str(uuid4()),
                "agent_strategy": "react",
            }

        def acquire_lease(self, account_id: str, run_id: str):
            raise LeaseConflict("busy")

    class WaitingEvents(Events):
        async def progress(self, phase: str, summary: str) -> None:
            phases.append(phase)

    class WaitingFactory:
        def for_run(self, run_id: str) -> WaitingEvents:
            return WaitingEvents()

    monkeypatch.setattr(web_activities, "_agent_store", BusyStore())
    monkeypatch.setattr(web_activities, "_agent_event_factory", WaitingFactory())
    result = await web_activities.acquire_execution_lease_activity(
        AcquireLeaseInput(1, str(uuid4()))
    )
    assert result["acquired"] is False
    assert phases == ["waiting_for_account_execution"]


@pytest.mark.asyncio
async def test_declared_provider_idempotency_argument_receives_operation_id():
    captured: dict = {}

    class Sandbox:
        def get_tool_metadata(self, tool_name: str):
            return {"idempotency_key_argument": "request_id"}

    class Sandboxes:
        def get_sandbox_for_session(self, session_id: str):
            return Sandbox()

    runtime = ActionRuntime(sandbox_manager=Sandboxes())

    async def execute(**kwargs):
        captured.update(kwargs)
        return {"output": "ok", "metadata": {}}

    runtime.execute = execute
    result = await runtime.execute_request(
        ActionRequest(
            "call-1", "provider_write",
            {"value": 1, "request_id": "model-controlled-value"},
        ),
        session_id="session",
        execution_id="run",
        idempotency_key="operation-123",
    )
    assert captured["arguments"]["request_id"] == "operation-123"
    assert result.metadata["idempotency_key"] == "operation-123"


@pytest.mark.asyncio
async def test_mcp_durable_metadata_is_explicit_and_undeclared_tools_fail_closed(tmp_path):
    config = tmp_path / "servers.yaml"
    config.write_text(
        """
servers:
  provider:
    url: https://example.invalid/mcp
    tools:
      create_record:
        side_effect_class: idempotent_write
        idempotency_key_argument: request_id
""",
        encoding="utf-8",
    )
    await MCPToolManager(str(config)).load_config()

    declared = _build_langchain_tool(
        CachedTool("create_record", "create", {}, "provider"),
        object(),
    )
    undeclared = _build_langchain_tool(
        CachedTool("delete_record", "delete", {}, "provider"),
        object(),
    )
    assert declared.metadata["side_effect_class"] == "idempotent_write"
    assert declared.metadata["idempotency_key_argument"] == "request_id"
    assert undeclared.metadata["side_effect_class"] == "unknown"


@pytest.mark.asyncio
async def test_mcp_rejects_invalid_durable_metadata(tmp_path):
    config = tmp_path / "servers.yaml"
    config.write_text(
        """
servers:
  provider:
    tools:
      create_record:
        side_effect_class: guessed_write
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid side_effect_class"):
        await MCPToolManager(str(config)).load_config()
