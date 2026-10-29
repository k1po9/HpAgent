from uuid import UUID

import pytest
from support.qq_messages import qq_message

from application.qq_delivery import QQDeliveryAdapter, QQDeliveryService
from common.channel_result import ChannelSendResult
from conversation_domain.commands import CommandService
from web_persistence.test_qq_canonical_ingress import bind, service

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class Router:
    def __init__(self):
        self.sent = []
        self.ok = False

    async def send_with_receipt(self, message):
        self.sent.append(message)
        return ChannelSendResult("accepted" if self.ok else "failed", provider_message_id="-7" if self.ok else None)


async def test_delivery_failure_retries_committed_result_without_execution(
    db, account_id, worker_database_url
):
    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(), "onebot11")
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
    assert "committed answer" in router.sent[1].metadata["onebot_segments"][-1]["data"]["text"]
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
    result = await service(worker_database_url).accept(qq_message(), "onebot11")
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
    assert router.sent[1].metadata["onebot_segments"] == [{"type": "text", "data": {"text": "a" * 100}}]
    assert db.execute("SELECT state FROM deliveries").fetchone()[0] == "accepted"


async def test_long_chat_result_is_referenced_without_copying_it_into_notification(
    db, account_id, worker_database_url
):
    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(), "onebot11")
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
    assert router.sent[0].metadata["onebot_segments"][-1]["data"]["text"] == content[:1500]


@pytest.mark.parametrize("scope", ["private", "group"])
async def test_timeout_notice_is_deduplicated_and_delivered_without_success_message(
    db, account_id, worker_database_url, scope
):
    from web_domain.lifecycle import WebRunLifecycleService

    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(scope=scope), "onebot11")
    run_id = UUID(result["run_id"])
    CommandService(worker_database_url).start_run(account_id, run_id)
    lifecycle = WebRunLifecycleService(worker_database_url, qq_timeout_reply="configured timeout notice")
    lifecycle.finalize_failed(run_id, "model_read_timeout", "timeout")
    lifecycle.finalize_failed(run_id, "model_read_timeout", "timeout")
    assert db.execute("SELECT status,failure_code FROM runs WHERE run_id=%s", (run_id,)).fetchone() == (
        "failed", "model_read_timeout",
    )
    assert db.execute("SELECT status FROM messages WHERE produced_by_run_id=%s", (run_id,)).fetchone() == ("failed",)
    assert db.execute("SELECT count(*) FROM notifications WHERE run_id=%s", (run_id,)).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 1
    router = Router()
    router.ok = True
    sender = QQDeliveryService(worker_database_url, QQDeliveryAdapter(router))
    await sender.deliver_once()
    await sender.deliver_once()
    assert len(router.sent) == 1
    segments = router.sent[0].metadata["onebot_segments"]
    assert any(part["type"] == "text" and part["data"]["text"] == "configured timeout notice" for part in segments)
    assert any(part["type"] == "reply" for part in segments)
    assert any(part["type"] == "at" for part in segments) == (scope == "group")
    assert db.execute("SELECT state FROM deliveries").fetchone()[0] == "accepted"


@pytest.mark.parametrize(("code", "reply"), [
    ("model_connection_failed", "configured timeout notice"),
    ("model_read_timeout", ""),
])
async def test_other_failure_or_disabled_prompt_does_not_notify(
    db, account_id, worker_database_url, code, reply
):
    from web_domain.lifecycle import WebRunLifecycleService

    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(), "onebot11")
    run_id = UUID(result["run_id"])
    CommandService(worker_database_url).start_run(account_id, run_id)
    WebRunLifecycleService(worker_database_url, qq_timeout_reply=reply).finalize_failed(run_id, code, "failed")
    assert db.execute("SELECT count(*) FROM notifications WHERE run_id=%s", (run_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("revoke_during_enqueue", [False, True])
async def test_revoked_binding_does_not_prevent_timeout_failure_commit(
    db, account_id, worker_database_url, monkeypatch, revoke_during_enqueue,
):
    from delivery import service as delivery_service
    from web_domain.lifecycle import WebRunLifecycleService

    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(), "onebot11")
    run_id = UUID(result["run_id"])
    CommandService(worker_database_url).start_run(account_id, run_id)
    def revoke():
        db.execute("UPDATE identity_bindings SET status='revoked',revoked_at=now() WHERE account_id=%s", (account_id,))

    if revoke_during_enqueue:
        authorize = delivery_service.target_authorized
        checks = 0

        def check(uow, target):
            nonlocal checks
            checks += 1
            if checks == 2:
                revoke()
            return authorize(uow, target)

        monkeypatch.setattr(delivery_service, "target_authorized", check)
    else:
        revoke()
    WebRunLifecycleService(worker_database_url, qq_timeout_reply="timeout").finalize_failed(
        run_id, "model_read_timeout", "timeout",
    )
    assert db.execute("SELECT status,failure_code FROM runs WHERE run_id=%s", (run_id,)).fetchone() == (
        "failed", "model_read_timeout",
    )
    assert db.execute("SELECT status FROM messages WHERE produced_by_run_id=%s", (run_id,)).fetchone() == ("failed",)
    assert db.execute("SELECT count(*) FROM notifications WHERE run_id=%s", (run_id,)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM delivery_targets WHERE account_id=%s", (account_id,)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM outbox_events WHERE run_id=%s AND event_type='publish_terminal_event'", (run_id,)).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM outbox_events WHERE run_id=%s AND event_type='retain_memory'", (run_id,)).fetchone()[0] == 0


async def test_timeout_notice_is_frozen_and_revocation_before_send_cancels_delivery(
    db, account_id, worker_database_url,
):
    from web_domain.lifecycle import WebRunLifecycleService

    bind(db, account_id)
    result = await service(worker_database_url).accept(qq_message(), "onebot11")
    run_id = UUID(result["run_id"])
    CommandService(worker_database_url).start_run(account_id, run_id)
    WebRunLifecycleService(worker_database_url, qq_timeout_reply="original notice").finalize_failed(
        run_id, "model_read_timeout", "timeout",
    )
    WebRunLifecycleService(worker_database_url, qq_timeout_reply="changed notice").finalize_failed(
        run_id, "model_read_timeout", "timeout",
    )
    assert db.execute("SELECT payload->>'content' FROM notifications WHERE run_id=%s", (run_id,)).fetchall() == [("original notice",)]
    db.execute("UPDATE identity_bindings SET status='revoked',revoked_at=now() WHERE account_id=%s", (account_id,))
    router = Router()
    assert not await QQDeliveryService(worker_database_url, QQDeliveryAdapter(router)).deliver_once()
    assert not router.sent
    assert db.execute("SELECT state,last_error FROM deliveries").fetchone() == ("cancelled", "authority_revoked")
    assert db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone() == ("failed",)
