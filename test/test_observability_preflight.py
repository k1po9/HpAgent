from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from agent_execution.brain_action_loop import DefaultBrainActionLoop
from agent_execution.facade import (
    ExecutionRequest,
    ExecutionResult,
    NullExecutionAuditSink,
    StableExecutionFailure,
)
from agent_execution.qq_host import (
    QQExecutionHost,
    QQLegacyContextProvider,
    TurnMemoryQQRetentionSink,
    qq_execution_id,
)
from agent_execution.web_host import WebExecutionHost
from application.context_assembly import ContextAssemblyService, WebContextBase


class _Control:
    deadline = datetime.max.replace(tzinfo=UTC)

    def cancelled(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        return None

    async def heartbeat(self, phase: str) -> None:
        return None


class _Events:
    async def progress(self, phase: str, summary: str) -> None:
        return None


class _Actions:
    def reset_turn(self, session_id: str, execution_id: str) -> None:
        return None

    async def select_tools(self, **kwargs: Any) -> list[Any]:
        return []


def _request(*, surface: str = "web") -> ExecutionRequest:
    metadata = {"surface": surface}
    if surface == "web":
        metadata["run_id"] = "run-1"
    return ExecutionRequest(
        execution_id="run-1" if surface == "web" else "execution-1",
        account_id="account-1",
        conversation_id="conversation-1" if surface == "web" else "",
        session_id="session-1",
        user_content="question",
        context=(),
        metadata=metadata,
    )


def _decision(content: str = "answer") -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        action_requests=(),
        has_actions=False,
        stop_reason="stop",
        input_context=None,
    )


def _event_records(caplog: pytest.LogCaptureFixture, phase: str) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if getattr(record, "phase", None) == phase
        and getattr(record, "component", None) == "model"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "failure", "expected_code"),
    [
        ("agent_turn", None, None),
        ("agent_turn", TimeoutError("timeout"), "model_timeout"),
        ("agent_turn", RuntimeError("unavailable"), "model_unavailable"),
        ("forced_final", None, None),
        ("forced_final", TimeoutError("timeout"), "model_timeout"),
        ("forced_final", RuntimeError("unavailable"), "model_unavailable"),
    ],
)
async def test_model_call_lifecycle_is_complete(
    caplog: pytest.LogCaptureFixture,
    phase: str,
    failure: Exception | None,
    expected_code: str | None,
) -> None:
    class Brain:
        async def generate_chat_decision(self, **kwargs: Any) -> SimpleNamespace:
            if failure is not None:
                raise failure
            return _decision()

        async def generate_final_decision(self, **kwargs: Any) -> SimpleNamespace:
            if failure is not None:
                raise failure
            return _decision()

    loop = DefaultBrainActionLoop(
        Brain(), _Actions(), max_tool_turns=0 if phase == "forced_final" else 1
    )
    with caplog.at_level(logging.INFO, logger="HpAgent.BrainActionLoop"):
        if failure is None:
            result = await loop.execute(
                _request(), _Control(), _Events(), NullExecutionAuditSink()
            )
            assert result.content == "answer"
        else:
            with pytest.raises(StableExecutionFailure) as raised:
                await loop.execute(
                    _request(), _Control(), _Events(), NullExecutionAuditSink()
                )
            assert raised.value.code == expected_code

    records = _event_records(caplog, phase)
    assert [record.event for record in records] == [
        "model_call_started",
        "model_call_completed" if failure is None else "model_call_failed",
    ]
    terminal = records[-1]
    assert terminal.status == ("success" if failure is None else "failed")
    assert terminal.execution_id == "run-1"
    assert terminal.run_id == "run-1"
    if expected_code is not None:
        assert terminal.error_code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_code", [None, "tool_timeout"])
async def test_web_host_agent_lifecycle_has_full_correlation(
    caplog: pytest.LogCaptureFixture, failure_code: str | None
) -> None:
    request = _request()

    class Loader:
        async def load(self, run_id: str) -> ExecutionRequest:
            return request

    class Facade:
        async def execute(self, *args: Any) -> ExecutionResult:
            if failure_code:
                raise StableExecutionFailure(failure_code)
            return ExecutionResult("answer", 1)

    class EventFactory:
        def for_run(self, run_id: str) -> _Events:
            return _Events()

    class Replies:
        async def complete(self, run_id: str, result: ExecutionResult) -> None:
            return None

    host = WebExecutionHost(Loader(), Facade(), EventFactory(), Replies(), _Control())
    with caplog.at_level(logging.INFO, logger="HpAgent.WebExecutionHost"):
        if failure_code:
            with pytest.raises(StableExecutionFailure):
                await host.execute("run-1")
        else:
            await host.execute("run-1")

    records = [r for r in caplog.records if getattr(r, "component", None) == "agent"]
    assert [r.event for r in records] == [
        "agent_execution_started",
        "agent_execution_failed" if failure_code else "agent_execution_completed",
    ]
    for record in records:
        assert record.run_id == record.execution_id == "run-1"
        assert record.conversation_id == "conversation-1"
        assert record.session_id == "session-1"
        assert record.account_id == "account-1"
        assert record.surface == "web"
    if failure_code:
        assert records[-1].error_code == failure_code


@pytest.mark.asyncio
async def test_qq_host_agent_lifecycle_uses_execution_and_workflow_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = {
        "message_id": "message-1",
        "session_id": "session-1",
        "account_id": "account-1",
    }

    class Loader:
        async def load(self, user_message: dict[str, Any], execution_id: str) -> ExecutionRequest:
            request = _request(surface="qq")
            return ExecutionRequest(
                execution_id, request.account_id, "", request.session_id,
                request.user_content, request.context, metadata=request.metadata,
            )

    class Facade:
        async def execute(self, *args: Any) -> ExecutionResult:
            return ExecutionResult("answer", 1)

    class EventFactory:
        def for_execution(self, execution_id: str, user_message: dict[str, Any]) -> _Events:
            return _Events()

    class Replies:
        async def complete(self, user_message: dict[str, Any], result: ExecutionResult) -> None:
            return None

    host = QQExecutionHost(Loader(), Facade(), EventFactory(), Replies(), _Control())
    with caplog.at_level(logging.INFO, logger="HpAgent.QQExecutionHost"):
        await host.execute("workflow-1", message)

    records = [r for r in caplog.records if getattr(r, "component", None) == "agent"]
    assert [r.event for r in records] == [
        "agent_execution_started",
        "agent_execution_completed",
    ]
    for record in records:
        assert record.execution_id == qq_execution_id("workflow-1", "message-1")
        assert record.workflow_id == "workflow-1"
        assert record.session_id == "session-1"
        assert record.account_id == "account-1"
        assert record.surface == "qq"
        assert not hasattr(record, "run_id")


@pytest.mark.asyncio
async def test_qq_memory_recall_reports_degradation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Memory:
        async def recall_memories_with_status(self, **kwargs: Any):
            return [], "", True

    class Context:
        def build(self, **kwargs: Any) -> list[Any]:
            return []

    message = {
        "session_id": "session-1",
        "account_id": "account-1",
        "channel_type": "napcat",
        "content": "question",
        "metadata": {"execution_id": "execution-1"},
    }
    provider = QQLegacyContextProvider(Memory(), Context(), [], message, "")

    with caplog.at_level(logging.INFO, logger="HpAgent.QQExecutionHost"):
        assert await provider.recall_long_term("rewritten") == ("",)

    records = [r for r in caplog.records if getattr(r, "component", None) == "memory"]
    assert [r.event for r in records] == [
        "memory_recall_started",
        "memory_recall_degraded",
    ]
    assert records[-1].status == "degraded"
    assert records[-1].error_code == "memory_backend_unavailable"
    assert records[-1].execution_id == "execution-1"


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
async def test_qq_memory_retain_failure_is_logged_and_preserved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Memory:
        async def retain_document(self, **kwargs: Any) -> None:
            raise ConnectionError("memory unavailable")

    request = _request(surface="qq")
    message = {"channel_type": "napcat", "metadata": {}}
    sink = TurnMemoryQQRetentionSink(Memory())

    with caplog.at_level(logging.INFO, logger="HpAgent.QQExecutionHost"):
        with pytest.raises(ConnectionError):
            await sink.retain(request, ExecutionResult("answer", 1), message)

    records = [r for r in caplog.records if getattr(r, "component", None) == "memory"]
    assert [r.event for r in records] == [
        "memory_retain_started",
        "memory_retain_failed",
    ]
    assert records[-1].status == "failed"
    assert records[-1].error_code == "memory_retain_failed"
    assert records[-1].execution_id == "execution-1"
