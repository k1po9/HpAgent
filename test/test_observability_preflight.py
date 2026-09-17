from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

from application.context_assembly import ContextAssemblyService, WebContextBase


@pytest.mark.asyncio
async def test_web_memory_recall_reports_skipped_when_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    base = WebContextBase(
        run_id=uuid4(),
        account_id=uuid4(),
        conversation_id=uuid4(),
        session_id=uuid4(),
        context_message_seq=1,
        trigger_message_id=uuid4(),
        trigger_content="question",
        short_term_events=(),
    )
    service = ContextAssemblyService(None, SimpleNamespace(), hindsight=None)

    with caplog.at_level(logging.INFO, logger="HpAgent.ContextAssembly"):
        assert await service.recall_long_term(base, "rewritten") == ()

    records = [r for r in caplog.records if getattr(r, "component", None) == "memory"]
    assert [r.event for r in records] == ["memory_recall_skipped"]
    assert records[0].status == "skipped"
    assert records[0].reason == "memory_disabled"
    assert records[0].run_id == records[0].execution_id == str(base.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("final_only", [False, True])
@pytest.mark.parametrize("failure", [None, TimeoutError, RuntimeError])
async def test_durable_model_lifecycle_preserves_correlation_and_cleanup(caplog, final_only, failure):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, Mock

    from temporalio.exceptions import ApplicationError

    from agent_activities.runtime import DurableAgentActivities
    from agent_workflows.contracts import (
        AGENT_SCHEMA_VERSION,
        ChatContext,
        ModelDecisionInput,
        RunContext,
        RunSource,
    )
    from brain.contracts import BrainDecision
    from conversation_domain.execution_bindings import ChatExecutionBindings

    run_id, account_id, conversation_id, session_id = [str(uuid4()) for _ in range(4)]
    request = ModelDecisionInput(
        AGENT_SCHEMA_VERSION, run_id, account_id, RunSource("chat", conversation_id),
        RunContext(chat=ChatContext(conversation_id, session_id, None), surface="napcat"),
        "react", "transcript", 1, 1, f"{run_id}:model:1", 1, final_only=final_only,
    )
    store = SimpleNamespace(
        begin_operation=Mock(return_value=None),
        validate_and_renew_lease=Mock(),
        load_messages=Mock(return_value=([{"role": "user", "content": "hi"}], 1)),
        complete_operation_with_event=Mock(return_value=2),
        fail_operation=Mock(),
    )
    actions = SimpleNamespace(reset_execution=Mock(), select_tools=AsyncMock(return_value=[]),
                              clear_execution=Mock())
    events = SimpleNamespace(progress=AsyncMock(), trace_start=AsyncMock(),
                             trace_end=AsyncMock(), close=AsyncMock())
    decision = BrainDecision("answer", [], "stop", {})
    brain = SimpleNamespace(
        generate_chat_decision=AsyncMock(return_value=decision, side_effect=failure),
        generate_final_decision=AsyncMock(return_value=decision, side_effect=failure),
    )

    @asynccontextmanager
    async def workspace(*args):
        yield

    runtime = DurableAgentActivities(
        context_bindings=ChatExecutionBindings(), store=store, loader=None,
        brain=brain, actions=actions, event_factory=SimpleNamespace(for_run=lambda _: events),
        resource_prep=SimpleNamespace(lease_for_run=workspace), lifecycle=None,
    )
    with caplog.at_level(logging.INFO, logger="HpAgent.ModelDecisionActivity"):
        if failure:
            with pytest.raises(ApplicationError) as raised:
                await runtime.model_decision(request)
            assert raised.value.type == "model_unavailable"
            store.fail_operation.assert_called_once_with(request.operation_id, "model_unavailable")
        else:
            assert (await runtime.model_decision(request)).decision_type == "final"
            store.complete_operation_with_event.assert_called_once()
    records = [r for r in caplog.records if getattr(r, "component", None) == "model"]
    assert [r.event for r in records] == [
        "model_decision_started", "model_decision_failed" if failure else "model_decision_completed",
    ]
    for record in records:
        assert record.run_id == record.execution_id == run_id
        assert record.account_id == account_id
        assert record.conversation_id == conversation_id
    events.close.assert_awaited_once()
    actions.clear_execution.assert_called_once_with(session_id, run_id)
