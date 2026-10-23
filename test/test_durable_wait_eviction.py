"""Destroying a cached Workflow is distinct from authoritative cancellation."""

from datetime import UTC, datetime, timedelta

from agent_workflows import segments
from agent_workflows.lifecycle_contracts import FinishWaitInput, WaitInput


def test_evicted_wait_does_not_issue_a_finish_command(monkeypatch):
    calls = []

    async def control(name, request, result_type=None):
        calls.append((name, request))
        return True

    class Suspended:
        def __await__(self):
            yield "waiting"

    monkeypatch.setattr(segments, "_control", control)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(segments.workflow, "now", lambda: now)
    monkeypatch.setattr(segments.workflow, "wait_condition", lambda *a, **kw: Suspended())
    request = WaitInput(
        1,
        "run",
        "account",
        "wait",
        "operation",
        "approval",
        "ref",
        (now + timedelta(minutes=1)).isoformat(),
        execution_id="execution",
    )
    wait = segments.DurableWait()
    coroutine = wait.run(request)
    assert coroutine.send(None) == "waiting"
    coroutine.close()  # Temporal destroys cached instances before replaying them.
    assert wait.wait_id is None
    assert calls == [("begin_agent_wait_activity", request)]
    assert not any(isinstance(value, FinishWaitInput) for _, value in calls)
