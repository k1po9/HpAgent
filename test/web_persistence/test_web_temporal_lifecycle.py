from __future__ import annotations

import asyncio
import threading
from uuid import uuid4

import pytest

from run_domain.lifecycle import RunLifecycleService
from web_domain.errors import RunNotCancellable
from web_domain.lifecycle import WebRunLifecycleService

from .test_phase_a_invariants import _conversation_and_run

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest.mark.parametrize("first", ["complete", "finalize_cancelled"])
async def test_td_007_cancel_and_complete_race_has_one_database_terminal(
    db, account_id, database_url, worker_database_url, first
):
    commands, _, run_id = _conversation_and_run(database_url, account_id)
    lifecycle = WebRunLifecycleService(worker_database_url)
    await asyncio.to_thread(lifecycle.prepare, run_id)
    assert commands.cancel_run(account_id, run_id, str(uuid4())).body["run"]["status"] == "cancelling"
    committed = asyncio.Event()

    async def ordered(name, function, *args):
        if name != first:
            await asyncio.wait_for(committed.wait(), 5)
        try:
            return await asyncio.to_thread(function, *args)
        finally:
            if name == first:
                committed.set()

    completed, cancelled = await asyncio.gather(
        ordered("complete", lifecycle.complete, run_id, "race reply"),
        ordered("finalize_cancelled", lifecycle.finalize_cancelled, run_id),
    )
    run_status = db.execute(
        "SELECT status FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone()[0]
    message = db.execute(
        "SELECT status,content FROM messages WHERE produced_by_run_id=%s", (run_id,)
    ).fetchone()
    terminal_events = db.execute(
        "SELECT count(*) FROM outbox_events "
        "WHERE run_id=%s AND event_type='publish_terminal_event'",
        (run_id,),
    ).fetchone()[0]

    assert run_status == cancelled.status == "cancelled"
    assert completed.status == ("cancelling" if first == "complete" else "cancelled")
    assert message == ("aborted", None)
    assert terminal_events == 1
    assert lifecycle.finalize_cancelled(run_id).status == "cancelled"
    assert lifecycle.complete(run_id, "late reply").status == "cancelled"
    assert db.execute("SELECT status,content FROM messages WHERE produced_by_run_id=%s", (run_id,)).fetchone() == message
    assert db.execute("SELECT count(*) FROM outbox_events WHERE run_id=%s AND event_type='publish_terminal_event'", (run_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("winner", ["complete", "cancel"])
async def test_running_completion_and_cancel_lock_order(
    db, account_id, database_url, worker_database_url, monkeypatch, winner
):
    """Both calls start running; fix commit order at the completion lock."""
    commands, _, run_id = _conversation_and_run(database_url, account_id)
    lifecycle = WebRunLifecycleService(worker_database_url)
    lifecycle.prepare(run_id)
    entered, release = threading.Event(), threading.Event()
    context = threading.local()
    original_lock = RunLifecycleService.lock

    def gated_lock(uow, owner, target):
        if winner == "cancel" and getattr(context, "completing", False):
            entered.set()
            assert release.wait(5), "cancel did not commit"
        return original_lock(uow, owner, target)

    monkeypatch.setattr(RunLifecycleService, "lock", staticmethod(gated_lock))
    def complete():
        context.completing = True
        try:
            return lifecycle.complete(run_id, "winner reply")
        finally:
            context.completing = False

    completion = asyncio.create_task(asyncio.to_thread(complete))
    try:
        if winner == "cancel":
            assert await asyncio.to_thread(entered.wait, 5)
            cancelled = commands.cancel_run(account_id, run_id, str(uuid4()))
            assert cancelled.body["run"]["status"] == "cancelling"
            release.set()
            assert (await completion).status == "cancelling"
            assert lifecycle.finalize_cancelled(run_id).status == "cancelled"
            expected = "cancelled"
            message = ("aborted", None)
        else:
            assert (await completion).status == "succeeded"
            with pytest.raises(RunNotCancellable):
                commands.cancel_run(account_id, run_id, str(uuid4()))
            expected = "succeeded"
            message = ("completed", "winner reply")
        assert lifecycle.finalize_cancelled(run_id).status == expected
        assert lifecycle.complete(run_id, "late reply").status == expected
        assert db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0] == expected
        assert db.execute("SELECT status,content FROM messages WHERE produced_by_run_id=%s", (run_id,)).fetchone() == message
        events = db.execute("SELECT payload->>'terminal_status' FROM outbox_events WHERE run_id=%s AND event_type='publish_terminal_event'", (run_id,)).fetchall()
        assert events == [(expected,)]
    finally:
        release.set()
        await completion


async def test_td_010_retried_lifecycle_finalizer_is_logically_idempotent(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    lifecycle = WebRunLifecycleService(worker_database_url)
    await asyncio.to_thread(lifecycle.prepare, run_id)

    first = await asyncio.to_thread(
        lifecycle.finalize_failed, run_id, "model_unavailable", "safe"
    )
    second = await asyncio.to_thread(
        lifecycle.finalize_failed, run_id, "different_late_error", "ignored"
    )

    row = db.execute(
        "SELECT status,failure_code,failure_message FROM runs WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    terminal_events = db.execute(
        "SELECT count(*) FROM outbox_events "
        "WHERE run_id=%s AND event_type='publish_terminal_event'",
        (run_id,),
    ).fetchone()[0]
    assert first.status == second.status == "failed"
    assert row == ("failed", "model_unavailable", "safe")
    assert terminal_events == 1
