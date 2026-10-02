from uuid import UUID

import pytest
from support.qq_messages import qq_message

from application.qq_delivery import QQDeliveryAdapter, QQDeliveryService
from conversation_domain.commands import CommandService
from web_persistence.test_qq_canonical_ingress import bind, service

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class Router:
    def __init__(self):
        self.sent = []
        self.ok = False

    async def send(self, message):
        self.sent.append(message)
        return self.ok


async def test_delivery_failure_retries_committed_result_without_execution(
    db, account_id, worker_database_url
):
    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(), "napcat")
    run_id = UUID(result["run_id"])
    commands = CommandService(worker_database_url)
    assert db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 0
    commands.start_run(account_id, run_id)
    commands.complete_run(account_id, run_id, "committed answer")
    commands.complete_run(account_id, run_id, "different replay content")
    assert db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 1
    router = Router()
    sender = QQDeliveryService(worker_database_url, QQDeliveryAdapter(router))
    await sender.deliver_once()
    assert db.execute("SELECT state,last_error FROM deliveries").fetchone() == ("failed", "failed")
    delivery_id = db.execute("SELECT delivery_id FROM deliveries").fetchone()[0]
    sender.retry_uncertain(account_id, delivery_id)
    router.ok = True
    await QQDeliveryService(worker_database_url, QQDeliveryAdapter(router)).deliver_once()
    assert router.sent[0].content == router.sent[1].content
    assert "committed answer" in router.sent[1].content
    assert db.execute("SELECT state FROM deliveries").fetchone()[0] == "accepted"
    assert db.execute("SELECT status FROM runs").fetchall() == [("succeeded",)]
    assert (
        db.execute("SELECT count(*) FROM outbox_events WHERE event_type='start_run'").fetchone()[0]
        == 1
    )
    assert not await sender.deliver_once()


async def test_partial_chunks_resume_and_lost_sender_is_uncertain(
    db, account_id, worker_database_url
):
    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(), "napcat")
    run_id = UUID(result["run_id"])
    commands = CommandService(worker_database_url)
    commands.start_run(account_id, run_id)
    commands.complete_run(account_id, run_id, "a" * 1600)
    router = Router()
    router.ok = True
    db.execute("UPDATE deliveries SET last_error='previous failure'")
    sender = QQDeliveryService(worker_database_url, QQDeliveryAdapter(router))
    await sender.deliver_once()
    assert db.execute("SELECT next_part,state,last_error FROM deliveries").fetchone() == (
        1,
        "pending",
        None,
    )
    assert db.execute("SELECT octet_length(payload::text) FROM notifications").fetchone()[0] < 500
    db.execute("UPDATE deliveries SET available_at=now()")
    row = sender.claim()
    assert sender.claim() is None
    db.execute("UPDATE deliveries SET lease_until=now()-interval '1 second'")
    assert sender.claim() is None
    sender.finish(row, "accepted", 2, {"level": "adapter_accepted"})
    assert db.execute("SELECT next_part,state FROM deliveries").fetchone() == (1, "uncertain")

    assert not sender.retry_uncertain(UUID(int=0), row["delivery_id"])
    assert sender.retry_uncertain(account_id, row["delivery_id"])
    await sender.deliver_once()
    assert len(router.sent) == 2
    assert router.sent[1].content == "a" * 100
    assert db.execute("SELECT state FROM deliveries").fetchone()[0] == "accepted"


async def test_long_chat_result_is_referenced_without_copying_it_into_notification(
    db, account_id, worker_database_url
):
    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(), "napcat")
    run_id = UUID(result["run_id"])
    commands = CommandService(worker_database_url)
    commands.start_run(account_id, run_id)
    content = "x" * 40000
    commands.complete_run(account_id, run_id, content)
    assert (
        db.execute(
            "SELECT octet_length(payload::text),source_message_id FROM notifications"
        ).fetchone()[0]
        < 500
    )
    router = Router()
    sender = QQDeliveryService(worker_database_url, QQDeliveryAdapter(router))
    await sender.deliver_once()
    assert router.sent[0].content.endswith(content[:1500])
