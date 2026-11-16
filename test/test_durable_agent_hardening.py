from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg import OperationalError
from support.runtime_fakes import (
    ExecutionStoreBoundary,
    install_execution_uow,
    install_model_dispatch_guards,
)
from temporalio.exceptions import ApplicationError

from actions.contracts import ActionRequest, ActionResult
from actions.runtime import ActionRuntime
from agent_activities.runtime import DurableAgentActivities
from agent_activities.segments import SegmentActivities
from agent_activities.side_effects import UnsupportedToolSideEffectReconciler
from agent_activities.store import ToolOperationState
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    ChatContext,
    CompactToolCall,
    PlanEvaluationInput,
    PlanningInput,
    RunContext,
    RunSource,
    ToolExecutionInput,
)
from agent_workflows.ids import root_execution_id
from agent_workflows.lifecycle_contracts import SegmentInput
from conversation_domain.execution_bindings import ChatExecutionBindings
from run_domain.execution import LeaseConflict, StaleFencingToken, TranscriptVersionConflict
from sandbox.tools.adapters.mcp import CachedTool, MCPToolManager, _build_langchain_tool
from web_domain.failures import is_failure_retryable


@pytest.fixture(autouse=True)
def direct_to_thread(monkeypatch):
    async def run(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("agent_activities.runtime.asyncio.to_thread", run)
    install_execution_uow(monkeypatch)
    install_model_dispatch_guards(monkeypatch)


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


class Store(ExecutionStoreBoundary):
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

    def validate_and_renew_lease(self, account_id: str, run_id: str, token: int, execution_id=None):
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
        schema_version=AGENT_SCHEMA_VERSION,
        run_id=run_id,
        account_id=str(uuid4()),
        strategy="react",
        transcript_id=f"transcript:{run_id}",
        transcript_version=1,
        turn=1,
        operation_id=f"{run_id}:tool:call-1",
        lease_token=11,
        tool_call=CompactToolCall("call-1", "write_tool", "decision:1#call-1"),
        source=RunSource("chat", str(uuid4())),
        context=RunContext(chat=ChatContext(str(uuid4()), str(uuid4()), None), surface="web"),
     execution_id=str(root_execution_id(run_id)))


def activities(store: Store, actions: Actions, **kwargs) -> DurableAgentActivities:
    return DurableAgentActivities(
        context_bindings=ChatExecutionBindings(),
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
@pytest.mark.parametrize('failure', [OperationalError('policy read unavailable'), asyncio.CancelledError()])
async def test_policy_read_failure_before_tool_dispatch_leaves_no_budget_hold(failure):
    class PolicyStore(Store):
        def agent_policy(self, run_id):
            raise failure

    class Budget:
        def __init__(self):
            self.reserved = {}
            self.used = {}

        def reserve(self, run_id, operation_id, amounts):
            self.reserved.update(amounts)

        def settle(self, run_id, operation_id, amounts, source):
            self.used.update(amounts)
            self.reserved.clear()

        def release(self, run_id, operation_id):
            self.reserved.clear()

    budget = Budget()
    store, actions = PolicyStore(ToolOperationState('started', None)), Actions('read_only')
    expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else ApplicationError
    with pytest.raises(expected):
        await activities(store, actions, run_budget=budget).tool_execution(request())
    assert actions.calls == 0
    assert not budget.reserved and not budget.used
    assert not store.uncertain


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
    import json
    observation = json.loads(event["message"]["content"])
    assert observation["success"] is False
    assert observation["error"] == "mock read error"
    assert observation["result_processing"]["raw_available"] is True
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
        context_bindings=ChatExecutionBindings(),
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

        def validate_and_renew_lease(self, account_id: str, run_id: str, token: int, execution_id=None):
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
        context_bindings=ChatExecutionBindings(),
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
            schema_version=AGENT_SCHEMA_VERSION,
            run_id=run_id,
            account_id=identity[0],
            transcript_id=f"transcript:{run_id}",
            transcript_version=1,
            operation_id=f"{run_id}:plan:1:planner",
            lease_token=1,
            plan_id=f"plan:{run_id}",
            plan_version=1,
            source=RunSource("chat", identity[1]),
            context=RunContext(chat=ChatContext(identity[1], identity[2], None), surface="web"),
         execution_id=str(root_execution_id(run_id)))
    )
    assert len(planned.steps) == 1
    evaluated = await durable.evaluate_plan(
        PlanEvaluationInput(
            schema_version=AGENT_SCHEMA_VERSION,
            run_id=run_id,
            account_id=identity[0],
            transcript_id=f"transcript:{run_id}",
            transcript_version=1,
            operation_id=f"{run_id}:plan:1:step:step-1:evaluation",
            lease_token=1,
            plan_id=f"plan:{run_id}",
            plan_version=1,
            step_id="step-1",
            step_index=1,
            step_count=1,
            source=RunSource("chat", identity[1]),
            context=RunContext(chat=ChatContext(identity[1], identity[2], None), surface="web"),
         execution_id=str(root_execution_id(run_id)))
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
    assert is_failure_retryable("account_model_entitlement_unavailable") is False
    assert is_failure_retryable("account_daily_model_budget_exhausted") is False
    assert is_failure_retryable("model_access_tier_denied") is False


@pytest.mark.asyncio
async def test_busy_segment_returns_control_without_holding_activity_slot():
    class BusyStore:
        def acquire_segment(self, request):
            raise LeaseConflict("busy")

    result = await SegmentActivities(BusyStore()).acquire(
        SegmentInput(2, str(uuid4()), str(uuid4()), str(uuid4()), execution_id=str(root_execution_id(str(uuid4()))))
    )
    assert result.acquired is False
    assert result.fencing_token == 0


@pytest.mark.asyncio
async def test_declared_provider_idempotency_argument_receives_operation_id():
    captured: dict = {}

    class Sandbox:
        def get_tool_metadata(self, tool_name: str):
            return {"idempotency_key_argument": "request_id"}

    class Sandboxes:
        def get_sandbox_for_execution(self, session_id: str):
            return Sandbox()

    runtime = ActionRuntime(sandbox_manager=Sandboxes())

    async def execute(**kwargs):
        captured.update(kwargs)
        return {"output": "ok", "metadata": {}}

    runtime._execute = execute
    result = await runtime.execute_request(
        ActionRequest(
            "call-1", "provider_write",
            {"value": 1, "request_id": "model-controlled-value"},
        ),
        resource_key="session",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_names,classification,allowed",
    [([], "read_only", False), (["write_tool"], "read_only", True),
     (["write_tool"], "non_idempotent_write", False)],
)
async def test_subagent_tool_manifest_and_read_only_boundary(
    monkeypatch, tool_names, classification, allowed
):
    boundary = install_execution_uow(monkeypatch, role="subagent", tool_names=tool_names)
    store = Store(ToolOperationState("started", None))
    actions = Actions(classification)
    if allowed:
        assert (await activities(store, actions).tool_execution(request())).tool_success
        assert actions.calls == 1 and store.completion is not None
    else:
        with pytest.raises(ApplicationError) as denied:
            await activities(store, actions).tool_execution(request())
        assert denied.value.type == "delegation_scope_denied"
        assert denied.value.non_retryable
        assert actions.calls == 0 and store.completion is None
    assert boundary == ["fence", "execution"]


@pytest.mark.asyncio
async def test_initial_execution_fence_denial_prevents_tool_dispatch(monkeypatch):
    boundary = install_execution_uow(monkeypatch, fence_error=StaleFencingToken("taken over"))
    store = Store(ToolOperationState("started", None))
    actions = Actions("non_idempotent_write")
    with pytest.raises(ApplicationError) as denied:
        await activities(store, actions).tool_execution(request())
    assert denied.value.type == "stale_fencing_token"
    assert denied.value.non_retryable
    assert boundary == ["fence"]
    assert actions.calls == 0 and store.completion is None


@pytest.mark.asyncio
async def test_trusted_web_tool_bypasses_native_dispatch_and_outer_budget():
    from types import SimpleNamespace

    from agent_activities.web_tools import WebTools
    from research_domain.models import SourceCandidate
    from web_retrieval.service import WebRetrievalService

    class Search:
        async def discover(self, *args, **kwargs):
            return [SourceCandidate('https://docs.python.org/a', title='Tasks')]

    class Budget:
        def __init__(self):
            self.calls = []
        def reserve(self, run, operation, values, **kwargs):
            assert kwargs == {'enforce_limits': True}
            self.calls.append(('reserve', operation))
            return SimpleNamespace(replayed=False)
        def settle(self, run, operation, values, source):
            self.calls.append(('settle', operation))

    item = request()
    item = replace(item, tool_call=replace(item.tool_call, name='web_search'))
    store = Store(ToolOperationState('started', None))
    store.tool_call_arguments = lambda *args: {'query': 'TaskGroup'}
    native, budget = Actions('unknown'), Budget()
    runtime = activities(store, native, run_budget=budget,
        web_tools=WebTools(WebRetrievalService({'legacy': Search()}, {'local': object()}), budget))
    result = await runtime.tool_execution(item)
    assert result.tool_success and native.calls == 0
    assert len(budget.calls) == 2
    assert all(operation.endswith(':retrieval:search:v1') for _, operation in budget.calls)
    assert store.completion['event_payload']['side_effect_class'] == 'read_only'


@pytest.mark.asyncio
async def test_trusted_web_tool_is_denied_to_branch_even_if_listed(monkeypatch):
    install_execution_uow(monkeypatch, role='delegation', tool_names=('web_search',))
    item = request()
    item = replace(item, tool_call=replace(item.tool_call, name='web_search'))
    store = Store(ToolOperationState('started', None))
    with pytest.raises(ApplicationError) as error:
        await activities(store, Actions('read_only'), web_tools=object()).tool_execution(item)
    assert error.value.type == 'delegation_scope_denied'


@pytest.mark.parametrize('tool_name,group', [('web_search', 'web'), ('result__save', 'results')])
async def test_disabled_root_tool_is_denied_before_external_action(monkeypatch, tool_name, group):
    from application.tool_policy import ToolPolicy, tool_policy

    configured = tool_policy().model_dump()
    configured['roles']['main']['groups'] = tuple(
        name for name in configured['roles']['main']['groups'] if name != group
    )
    policy = ToolPolicy.model_validate(configured)
    monkeypatch.setattr('agent_activities.runtime.tool_policy', lambda: policy)
    item = request()
    item = replace(item, tool_call=replace(item.tool_call, name=tool_name))
    store = Store(ToolOperationState('started', None))
    native = Actions('read_only')
    runtime = activities(store, native, web_tools=object(), capabilities=object())
    with pytest.raises(ApplicationError) as error:
        await runtime.tool_execution(item)
    assert error.value.type == 'tool_policy_denied'
    assert native.calls == 0 and store.completion is None


@pytest.mark.parametrize('failure_kind', ['configuration', 'permission', 'program', 'failed_result', 'cancelled'])
async def test_tool_selection_failure_blocks_model_dispatch(tmp_path, failure_kind):
    from agent_workflows.contracts import ModelDecisionInput
    from sandbox.sandbox_manager import SandboxManager
    from sandbox.tools.routing.models import SemanticRetrievalResult, ToolRetrievalError

    class BrokenRetriever:
        async def retrieve(self, *args, **kwargs):
            if failure_kind == 'cancelled':
                raise asyncio.CancelledError
            if failure_kind == 'failed_result':
                return SemanticRetrievalResult(status='failed', error=ToolRetrievalError('query', 'profile_mismatch'))
            if failure_kind == 'configuration':
                raise ValueError('invalid configuration')
            if failure_kind == 'permission':
                raise PermissionError('denied')
            raise TypeError('invalid retrieval response')

    class ModelStore(Store):
        def begin_operation(self, *args):
            return None

    class Brain:
        calls = 0
        async def generate_chat_decision(self, **kwargs):
            self.calls += 1
            raise AssertionError('model dispatched after failed tool retrieval')

    original = request()
    item = ModelDecisionInput(**{key: value for key, value in vars(original).items()
                                if key != 'tool_call'})
    item = replace(item, objective='select tools for this request')
    manager = SandboxManager(nsjail_enabled=False, retriever=BrokenRetriever())
    native = ActionRuntime(sandbox_manager=manager)
    bindings = ChatExecutionBindings()
    manager.create_execution_sandbox(bindings.resource_key(item), str(tmp_path))
    store = ModelStore(ToolOperationState('started', None))
    brain = Brain()
    durable = DurableAgentActivities(context_bindings=bindings, store=store, loader=None,
        brain=brain, actions=native, event_factory=EventFactory(),
        resource_prep=ResourcePrep(), lifecycle=None)
    try:
        if failure_kind == 'cancelled':
            with pytest.raises(asyncio.CancelledError):
                await durable.model_decision(item)
            assert brain.calls == 0 and store.failed == []
            return
        with pytest.raises(ApplicationError) as caught:
            await durable.model_decision(item)
        assert caught.value.type.startswith('tool_retrieval_'), repr(caught.value.__cause__)
        assert brain.calls == 0
        assert store.failed == [caught.value.type]
        assert store.completion is None
    finally:
        manager.close()


@pytest.mark.parametrize('source,execution_role', [('chat', 'root'), ('work', 'root'), ('work', 'subagent')])
@pytest.mark.parametrize('selection_status', ['degraded', 'no_eligible_tools'])
async def test_manifest_preserves_main_delegation_boundaries_and_audit(tmp_path, monkeypatch, caplog, source, execution_role, selection_status):
    import json
    import logging

    from agent_activities.composition import compose_capabilities
    from agent_workflows.contracts import ModelDecisionInput
    from brain.contracts import BrainDecision
    from sandbox.sandbox_manager import SandboxManager

    install_execution_uow(monkeypatch, role=execution_role, tool_names=('fs_read', 'fs_write'))
    if source == 'chat':
        from persistence.uow import UnitOfWork

        class MainUow(UnitOfWork):
            def execute(self, sql, params):
                result = super().execute(sql, params)
                if 'SELECT input_snapshot FROM runs' in sql:
                    return SimpleNamespace(fetchone=lambda: {'input_snapshot': {'main_protocol': 'tool_first_v1'}})
                return result
        monkeypatch.setattr('persistence.uow.UnitOfWork', MainUow)

    class Down:
        async def retrieve(self, *args, **kwargs):
            raise TimeoutError('dependency unavailable')

    class ModelStore(Store):
        def begin_operation(self, *args):
            return None

    class Brain:
        manifest = None
        availability = None
        async def generate_chat_decision(self, *, messages, tools):
            self.manifest = [tool.get('function', tool)['name'] for tool in tools or []]
            availability = next(m['content'] for m in messages if '"delegatable_tools"' in m['content'])
            self.availability = json.loads(availability[availability.index('{'):])
            return BrainDecision('', [], 'stop', {})

    original = request()
    item = ModelDecisionInput(**{key: value for key, value in vars(original).items() if key != 'tool_call'})
    item = replace(item, source=RunSource(source, str(uuid4())), objective='read the workspace')
    bindings = ChatExecutionBindings()
    manager = SandboxManager(nsjail_enabled=False, retriever=Down(),
                             native_tools_enabled=selection_status != 'no_eligible_tools')
    manager.create_execution_sandbox(bindings.resource_key(item), str(tmp_path), session_context={
        'source_kind': source, 'execution_role': execution_role,
        'parent_tool_names': ['fs_read', 'fs_write'],
    })
    actions = ActionRuntime(sandbox_manager=manager, tool_rag_top_k=2)
    brain = Brain()
    durable = DurableAgentActivities(context_bindings=bindings, store=ModelStore(ToolOperationState('started', None)),
        loader=None, brain=brain, actions=actions, event_factory=EventFactory(), resource_prep=ResourcePrep(), lifecycle=None,
        capabilities=compose_capabilities(None, services=frozenset({'research', 'html', 'text'})), web_tools=object())
    caplog.set_level(logging.INFO)
    try:
        await durable.model_decision(item)
        audit = next(r for r in caplog.records if getattr(r, 'event', '') == 'tool_manifest_selected')
        assert audit.status == selection_status
        assert audit.ordinary_candidate_count <= 2
        assert audit.manifest_tool_count == len(brain.manifest)
        if execution_role == 'subagent':
            assert brain.manifest == (['fs_read'] if selection_status == 'degraded' else [])
            assert brain.availability['delegatable_tools'] == []
        else:
            assert audit.manifest_tool_count > audit.ordinary_candidate_count
            assert 'web_search' in brain.manifest
            assert 'result__save' in brain.manifest
            if source == 'chat':
                assert 'respond_directly' in brain.manifest
                assert 'delegate_work' not in brain.manifest
            else:
                assert 'delegate_work' in brain.manifest
                assert 'respond_directly' not in brain.manifest
                assert 'fs_write' not in brain.availability['delegatable_tools']
        if selection_status == 'degraded':
            assert audit.reasons[0]['code'] == 'tool_retrieval_dependency_unavailable'
        else:
            assert audit.reasons == []
    finally:
        manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['success', 'publish_failure', 'budget_denied', 'invalid_id', 'no_publisher', 'no_budget'])
async def test_large_result_publication_keeps_known_outcome_and_replays(tmp_path, mode, caplog):
    import json
    import logging

    caplog.set_level(logging.INFO)

    from resources.run_budget import RunBudgetExhausted
    from storage.tenant_file_store import TenantFileStore

    item = request()
    raw = {'success': True, 'output': {'body': '中' * 200000,
           'command_receipt': {'command_id': 'committed-command'}, 'file_id': str(uuid4())}}
    stored_files = TenantFileStore(tmp_path, max_bytes=4 * 1024 * 1024)
    calls, budget_calls = [], []
    file_id = uuid4()

    class Publisher:
        store = stored_files
        def publish(self, scope, operation, name, content_type, **kwargs):
            calls.append(operation)
            if mode == 'publish_failure':
                raise OSError('unavailable')
            data = (scope.outputs_root / name).read_bytes()
            staged = stored_files.stage(file_id, [data], declared_size=len(data))
            published = stored_files.publish(uuid4(), file_id, staged)
            with stored_files.open(published.storage_key) as stream:
                assert json.load(stream) == raw
            return SimpleNamespace(file_id='invalid' if mode == 'invalid_id' else file_id)

    class Budget:
        def reserve(self, run, operation, amounts, **kwargs):
            budget_calls.append(('reserve', operation, amounts))
            if mode == 'budget_denied' and 'output_file_bytes' in amounts:
                raise RunBudgetExhausted('rejected')
        def settle(self, run, operation, amounts, source):
            budget_calls.append(('settle', operation, amounts))
        def release(self, run, operation):
            budget_calls.append(('release', operation))

    class LargeActions(Actions):
        async def execute_request(self, action, **kwargs):
            self.calls += 1
            return ActionResult.from_runtime_result(action, raw)

    store = Store(ToolOperationState('started', None))
    actions = LargeActions('non_idempotent_write')
    activity = activities(store, actions,
        output_publisher=None if mode == 'no_publisher' else Publisher(),
        run_budget=None if mode == 'no_budget' else Budget())
    first = await activity.tool_execution(item)
    event = store.completion['event_payload']
    observation = json.loads(event['message']['content'])
    processing = observation['result_processing']
    assert first.tool_success is True
    assert processing['raw_available'] is (mode == 'success')
    assert ('raw_file_id' in processing) is (mode == 'success')
    completed_log = next(r for r in caplog.records if getattr(r, 'event', '') == 'tool_execution_completed')
    expected_ids = {raw['output']['file_id']} | ({str(file_id)} if mode == 'success' else set())
    assert set(completed_log.file_ids) == expected_ids
    assert completed_log.raw_available is (mode == 'success')
    assert len(json.dumps(event['raw_result']).encode()) <= 1048576
    assert observation['references']['command_receipt']['command_id'] == 'committed-command'
    assert observation['references']['file_id'] == raw['output']['file_id']
    assert not store.uncertain and not store.failed
    assert len(calls) == (0 if mode in {'budget_denied', 'no_publisher', 'no_budget'} else 1)
    if mode == 'success':
        amounts = {'bytes_written': len(json.dumps(raw).encode()),
                   'output_file_bytes': len(json.dumps(raw).encode())}
        assert ('reserve', calls[0], amounts) in budget_calls
        assert ('settle', calls[0], amounts) in budget_calls
    store.state = ToolOperationState('completed', store.completion['result_payload'])
    second = await activity.tool_execution(item)
    assert second == first and actions.calls == 1


@pytest.mark.asyncio
async def test_invalid_result_after_known_write_completes_failure_observation_without_retry():
    import json

    class CyclicActions(Actions):
        async def execute_request(self, action, **kwargs):
            self.calls += 1
            output = []
            output.append(output)
            return ActionResult.from_runtime_result(action, {'success': True, 'output': output})

    store = Store(ToolOperationState('started', None))
    action = CyclicActions('non_idempotent_write')
    result = await activities(store, action).tool_execution(request())
    assert result.tool_success is True and action.calls == 1
    assert not store.uncertain
    observation = json.loads(store.completion['event_payload']['message']['content'])
    assert observation['result_processing']['raw_available'] is False
    assert observation['result_processing']['processing_error']


@pytest.mark.asyncio
async def test_large_branch_result_cannot_acquire_output_publication(monkeypatch):
    import json

    install_execution_uow(monkeypatch, role='subagent', tool_names=('write_tool',))
    class BranchActions(Actions):
        async def execute_request(self, action, **kwargs):
            return ActionResult.from_runtime_result(action, {'success': True, 'output': 'x' * 1048576})
    publish_calls = []
    class Publisher:
        store = SimpleNamespace(max_bytes=4 * 1048576)
        def publish(self, *_args, **_kwargs):
            publish_calls.append(True)
            return SimpleNamespace(file_id=uuid4())
    class Budget:
        def reserve(self, *_args, **_kwargs):
            pass
        def settle(self, *_args, **_kwargs):
            pass
    store = Store(ToolOperationState('started', None))
    await activities(store, BranchActions('read_only'), output_publisher=Publisher(), run_budget=Budget()).tool_execution(request())
    processing = json.loads(store.completion['event_payload']['message']['content'])['result_processing']
    assert processing['raw_available'] is False and 'raw_file_id' not in processing
    assert not publish_calls


@pytest.mark.asyncio
async def test_success_display_summary_keeps_tool_preview_without_processing_metadata():
    result = await activities(Store(ToolOperationState('started', None)), Actions('read_only')).tool_execution(request())
    assert result.display_summary == 'ok'


@pytest.mark.parametrize('tool_error', [None, 'private tool failure'])
async def test_tool_completion_log_separates_outcome_and_safe_result_details(caplog, tool_error):
    import json
    import logging

    from common.logging import _JsonFormatter

    class ResultActions(Actions):
        async def execute_request(self, action, **kwargs):
            return ActionResult(
                request=action, success=tool_error is None,
                output={'file_id': '8a6f8763-949f-4e4f-b1c1-6d6936be8915',
                        'file_ids': ['6ee29531-f9cf-4f72-9923-ee9fa7eb606c', 'private-invalid-reference'],
                        'outputs': [{'file_id': '53426043-2234-454a-bc8d-8bbea68f1990', 'body': 'private-output-body'}],
                        'command_receipt': {'message': 'private-command-body'},
                        'body': 'private-result-body'},
                error=tool_error,
                raw={'success': tool_error is None, 'output': 'private-result-body'},
            )

    caplog.set_level(logging.INFO)
    item = request()
    result = await activities(Store(ToolOperationState('started', None)), ResultActions('read_only')).tool_execution(item)
    records = [json.loads(_JsonFormatter().format(r)) for r in caplog.records]
    completed = next(r for r in records if r.get('event') == 'tool_execution_completed')
    assert completed['status'] == 'success'
    assert completed['tool_success'] is (tool_error is None)
    assert completed['result_ref'] == result.result_ref
    assert completed['execution_id'] == item.execution_id
    assert completed['lease_token'] == 11
    assert completed['raw_json_bytes'] == len(json.dumps({'success': tool_error is None, 'output': 'private-result-body'}).encode())
    assert completed['observation_bytes'] > 0
    assert completed['file_ids'] == ['53426043-2234-454a-bc8d-8bbea68f1990',
                                      '6ee29531-f9cf-4f72-9923-ee9fa7eb606c',
                                      '8a6f8763-949f-4e4f-b1c1-6d6936be8915']
    assert 'private-invalid-reference' not in json.dumps(records)
    assert 'private-output-body' not in json.dumps(records)
    assert 'private-command-body' not in json.dumps(records)
    assert 'private-result-body' not in json.dumps(records)
    assert 'private tool failure' not in json.dumps(records)


async def test_tool_activity_exception_logged_once_without_exception_text(caplog):
    import json
    import logging

    from common.logging import _JsonFormatter

    class BrokenActions(Actions):
        async def execute_request(self, action, **kwargs):
            raise ValueError('private-body-and-credential')

    caplog.set_level(logging.INFO)
    item = request()
    with pytest.raises(ApplicationError):
        await activities(Store(ToolOperationState('started', None)), BrokenActions('read_only')).tool_execution(item)
    records = [json.loads(_JsonFormatter().format(r)) for r in caplog.records]
    failures = [r for r in records if r.get('event') == 'tool_execution_failed']
    assert len(failures) == 1
    assert failures[0]['error_type'] == 'ValueError'
    assert failures[0]['traceback']
    assert failures[0]['operation_id'] == item.operation_id
    assert 'private-body-and-credential' not in json.dumps(records)
