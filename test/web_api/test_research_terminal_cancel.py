"""Work Run terminal facts do not require chat messages or SSE."""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from support.work_fixtures import research_requirement

from conversation_domain.commands import CommandService
from web_api import terminal_publisher

pytestmark = pytest.mark.postgres


def _headers(csrf: str, key: str) -> dict[str, str]:
    return {"Origin": "https://testserver", "X-CSRF-Token": csrf,
            "Idempotency-Key": key}


def _work_run(seed_identity, client_factory, username: str):
    account_id = seed_identity(username)
    client = client_factory()
    assert client.post("/auth/login", json={"username": username,
        "password": "correct-password"}, follow_redirects=False).status_code == 303
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    work = client.post("/api/v1/works", json={"title": "Terminal check",
        "requirement": research_requirement("Check independent Work")},
        headers=_headers(csrf, str(uuid4())))
    assert work.status_code == 201, work.text
    work_id = work.json()["work"]["work_id"]
    triggered = client.post(f"/api/v1/works/{work_id}/advance", json={},
        headers={**_headers(csrf, str(uuid4())), "If-Match": work.headers["ETag"]})
    assert triggered.status_code == 202, triggered.text
    return account_id, client, csrf, work_id, UUID(triggered.json()["run"]["run_id"])


def _assert_no_chat_terminal(db, run_id: UUID):
    assert db.execute("SELECT count(*) FROM messages WHERE produced_by_run_id=%s",
                      (run_id,)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM outbox_events WHERE run_id=%s "
                      "AND event_type='publish_terminal_event'",
                      (run_id,)).fetchone()[0] == 0


def test_independent_queued_cancel_api_and_replay(
    seed_identity, client_factory, db,
):
    _, client, csrf, work_id, run_id = _work_run(
        seed_identity, client_factory, "task-queued-cancel")
    seed_identity("task-cancel-other-account")
    other = client_factory()
    assert other.post("/auth/login", json={"username": "task-cancel-other-account",
        "password": "correct-password"}, follow_redirects=False).status_code == 303
    other_csrf = other.get("/api/v1/me").json()["csrf_token"]
    key = str(uuid4())
    url = f"/api/v1/runs/{run_id}/cancel"
    assert other.post(url, json={}, headers=_headers(other_csrf, str(uuid4()))).status_code == 404
    first = client.post(url, json={}, headers=_headers(csrf, key))
    assert first.status_code == 200, first.text
    assert first.json()["source_kind"] == "work"
    assert first.json()["run"]["run_id"] == str(run_id)
    assert first.json()["run"]["status"] == "cancelled"
    assert "assistant_message" not in first.json()
    mandate = client.get(f"/api/v1/works/{work_id}").json()["work"]
    assert mandate["status"] == "active" and mandate["active_coordinator_run_id"] is None
    replay = client.post(url, json={}, headers=_headers(csrf, key))
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert client.post(url, json={}, headers=_headers(csrf, str(uuid4()))).json() == first.json()
    assert client.get(f"/api/v1/runs/{run_id}").json()["run"]["status"] == "cancelled"
    _assert_no_chat_terminal(db, run_id)


def test_independent_running_cancel_callback_and_normal_failure(
    seed_identity, client_factory, db, worker_database_url,
):
    account_id, client, csrf, work_id, run_id = _work_run(
        seed_identity, client_factory, "task-running-cancel")
    db.execute("UPDATE runs SET status='running',started_at=now() WHERE run_id=%s",
               (run_id,))
    url = f"/api/v1/runs/{run_id}/cancel"
    first = client.post(url, json={}, headers=_headers(csrf, str(uuid4())))
    assert first.status_code == 202, first.text
    assert first.json()["source_kind"] == "work"
    assert first.json()["run"]["run_id"] == str(run_id)
    assert first.json()["run"]["status"] == "cancelling"
    assert "assistant_message" not in first.json()
    assert db.execute("SELECT count(*) FROM outbox_events WHERE run_id=%s "
                      "AND event_type='cancel_run'", (run_id,)).fetchone()[0] == 1
    commands = CommandService(worker_database_url)
    assert commands.cancelled_run(account_id, run_id)
    assert commands.cancelled_run(account_id, run_id)
    assert client.get(f"/api/v1/runs/{run_id}").json()["run"]["status"] == "cancelled"
    _assert_no_chat_terminal(db, run_id)

    _, _, _, _, failed_id = _work_run(seed_identity, client_factory, "work-normal-failure")
    db.execute("UPDATE runs SET status='running',started_at=now() WHERE run_id=%s",
               (failed_id,))
    failed_account = db.execute("SELECT account_id FROM runs WHERE run_id=%s",
                                (failed_id,)).fetchone()[0]
    assert commands.fail_run(failed_account, failed_id, "test_failure")
    assert commands.fail_run(failed_account, failed_id, "test_failure")
    assert db.execute("SELECT status FROM runs WHERE run_id=%s",
                      (failed_id,)).fetchone()[0] == "failed"
    _assert_no_chat_terminal(db, failed_id)


def test_work_terminal_outbox_row_is_consumed_without_chat_sse(monkeypatch):
    class Outbox:
        def __init__(self):
            self.processed = []

        def mark_processed(self, event_id, worker_id):
            self.processed.append((event_id, worker_id))
            return True

    class Redis:
        async def publish(self, *_):
            raise AssertionError("Work must not publish chat SSE")

    def no_snapshot(*_):
        raise AssertionError("Work has no chat snapshot")

    monkeypatch.setattr(terminal_publisher, "load_run_snapshot", no_snapshot)
    publisher = terminal_publisher.TerminalEventPublisher.__new__(
        terminal_publisher.TerminalEventPublisher)
    publisher.outbox = Outbox()
    publisher.redis = Redis()
    publisher.worker_id = "terminal-publisher"
    event_id = uuid4()
    asyncio.run(publisher._publish_one({
        "outbox_event_id": event_id, "account_id": uuid4(),
        "run_id": uuid4(), "conversation_id": None,
        "payload": {"terminal_status": "cancelled", "terminal_event_id": str(uuid4())},
    }))
    assert publisher.outbox.processed == [(event_id, "terminal-publisher")]
