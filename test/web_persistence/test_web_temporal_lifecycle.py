from __future__ import annotations

import asyncio

import pytest

from web_domain.lifecycle import WebRunLifecycleService

from .test_phase_a_invariants import _conversation_and_run

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_td_007_cancel_and_complete_race_has_one_database_terminal(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    lifecycle = WebRunLifecycleService(worker_database_url)
    await asyncio.to_thread(lifecycle.prepare, run_id)
    db.execute(
        "UPDATE runs SET status='cancelling',version=version+1 WHERE run_id=%s",
        (run_id,),
    )

    completed, cancelled = await asyncio.gather(
        asyncio.to_thread(lifecycle.complete, run_id, "race reply"),
        asyncio.to_thread(lifecycle.finalize_cancelled, run_id),
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

    assert run_status in {"completed", "cancelled"}
    assert completed.status == cancelled.status == run_status
    assert message == (
        ("completed", "race reply")
        if run_status == "completed"
        else ("aborted", None)
    )
    assert terminal_events == 1


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
