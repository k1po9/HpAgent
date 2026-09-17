from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from support.qq_messages import qq_message

from agent_activities.store import AgentDataStore
from application.context_assembly import ContextAssemblyService
from application.context_builder import HarnessContextBuilder
from application.conversation import ConversationService, UnboundIdentity
from application.ingress import (
    BUSY_REPLY,
    IDENTITY_RESOLUTION_UNAVAILABLE_REPLY,
    UNBOUND_REPLY,
    MessageIngressService,
)
from application.memory_retention import MemoryRetentionService
from conversation_domain.commands import CommandService
from conversation_domain.run_input import ChatRunInputLoader
from conversation_domain.sessions import ConversationSessionService
from conversation_domain.surface_commands import SurfaceConversationCommands
from memory.hindsight_client import RetainReceipt
from web_domain.errors import ConversationBusy, IdempotencyConflict

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def bind(db, account_id, sender="123", channel="napcat"):
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,external_subject_id,"
        "normalized_subject_id,verified_at) VALUES (%s,%s,'qq',%s,%s,now())",
        (uuid4(), account_id, sender, f"{channel}:{sender}"),
    )


def service(url):
    return ConversationService(SurfaceConversationCommands(CommandService(url)))


async def test_redelivery_uses_stable_provider_id_and_one_atomic_run(db, account_id, worker_database_url):
    bind(db, account_id)
    adapter = service(worker_database_url)
    # UnifiedMessage's random local ID differs; external provider ID remains stable.
    one, two = await asyncio.gather(adapter.accept(qq_message(), "napcat"), adapter.accept(qq_message(), "napcat"))
    assert one["run_id"] == two["run_id"]
    assert one.replayed != two.replayed
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    assert db.execute("SELECT event_type FROM outbox_events").fetchall() == [("start_run",)]
    with pytest.raises(IdempotencyConflict):
        await adapter.accept(qq_message(content="changed"), "napcat")
    origin = db.execute("SELECT origin FROM messages WHERE role='user'").fetchone()[0]
    assert origin["external_message_id"] == "1" and origin["channel_type"] == "napcat"


async def test_restart_rebuilds_qq_state_from_postgres_authority(
    db, account_id, worker_database_url,
):
    bind(db, account_id)
    original = qq_message("restart-stable", "before restart")
    first = await service(worker_database_url).accept(original, "napcat")

    # A fresh surface/service instance has no in-memory SessionStore or WAL to
    # recover. Provider idempotency and the active Session come from PostgreSQL.
    replayed = await service(worker_database_url).accept(original, "napcat")
    assert replayed.replayed
    assert replayed["run_id"] == first["run_id"]
    assert replayed["session_id"] == first["session_id"]

    await service(worker_database_url).accept(
        qq_message("restart-cancel", "/cancel"), "napcat"
    )
    after = await service(worker_database_url).accept(
        qq_message("restart-next", "after restart"), "napcat"
    )
    assert after["session_id"] == first["session_id"]
    assert after["run_id"] != first["run_id"]
    assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 2


async def test_account_room_bot_thread_mapping_and_shared_session_rule(db, account_id, worker_database_url):
    bind(db, account_id)
    adapter = service(worker_database_url)
    messages = [qq_message(), qq_message(scope="group"), qq_message(scope="group", thread="t1"),
                qq_message(scope="group", room="43"), qq_message(bot="other-bot")]
    results = [await adapter.accept(message, "napcat") for message in messages]
    assert len({r["conversation_id"] for r in results}) == 5
    assert len({r["session_id"] for r in results}) == 5
    cid = UUID(results[0]["conversation_id"])
    commands = CommandService(worker_database_url)
    with pytest.raises(ConversationBusy):
        commands.send_message(account_id, cid, str(uuid4()), "Web shares this explicit Conversation")
    with pytest.raises(ConversationBusy):
        ConversationSessionService(worker_database_url).rotate_active(account_id, cid)
    await adapter.accept(qq_message("cancel", "/cancel"), "napcat")
    successor = ConversationSessionService(worker_database_url).rotate_active(account_id, cid)
    next_result = await adapter.accept(qq_message("2"), "napcat")
    assert next_result["session_id"] == str(successor)


async def test_busy_receipt_and_cancel_replays_never_target_a_later_run(db, account_id, worker_database_url):
    bind(db, account_id)
    adapter = service(worker_database_url)
    first = await adapter.accept(qq_message(), "napcat")
    busy = await adapter.accept(qq_message("2"), "napcat")
    assert busy["code"] == "conversation_busy"
    cancel = await adapter.accept(qq_message("c", "/cancel"), "napcat")
    assert cancel["run_id"] == first["run_id"] and cancel["status"] == "cancelled"
    second = await adapter.accept(qq_message("3"), "napcat")
    assert second["session_id"] == first["session_id"]
    db.execute("DELETE FROM idempotency_commands")
    repeated = await adapter.accept(qq_message("c", "/cancel"), "napcat")
    assert repeated.replayed and repeated["run_id"] == first["run_id"]
    assert (await adapter.accept(qq_message("2"), "napcat"))["code"] == "conversation_busy"
    assert db.execute("SELECT status FROM runs WHERE run_id=%s", (UUID(second["run_id"]),)).fetchone()[0] == "queued"
    other = await adapter.accept(qq_message("4", scope="group"), "napcat")
    refused = await adapter.accept(qq_message("x", f"/cancel {other['run_id']}"), "napcat")
    assert refused["code"] == "no_matching_run"
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 3


async def test_unbound_and_revoked_identity_cannot_create_or_replay(db, account_id, worker_database_url):
    adapter = service(worker_database_url)
    with pytest.raises(UnboundIdentity):
        await adapter.accept(qq_message(), "napcat")
    assert db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    bind(db, account_id)
    await adapter.accept(qq_message(), "napcat")
    db.execute("UPDATE identity_bindings SET status='revoked',revoked_at=now()")
    with pytest.raises(UnboundIdentity):
        await adapter.accept(qq_message(), "napcat")
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1


async def test_pg_failure_rolls_back_binding_origin_message_run_outbox(db, account_id, worker_database_url, monkeypatch):
    bind(db, account_id)
    commands = CommandService(worker_database_url)
    adapter = ConversationService(SurfaceConversationCommands(commands))
    def fail(*args, **kwargs):
        raise RuntimeError("write failed")
    with monkeypatch.context() as patch:
        patch.setattr(commands.outbox, "enqueue", fail)
        with pytest.raises(RuntimeError, match="write failed"):
            await adapter.accept(qq_message(), "napcat")
    for table in ("conversations", "conversation_bindings", "conversation_ingress_receipts", "messages", "sessions", "runs", "outbox_events"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert (await adapter.accept(qq_message(), "napcat"))["run_id"]


class Reply:
    def __init__(self): self.sent = []
    async def send_final(self, text, message): self.sent.append(text)


async def test_ingress_distinguishes_auth_failure_database_failure_and_busy(db, account_id, worker_database_url):
    reply = Reply()
    ingress = MessageIngressService(conversation_service=service(worker_database_url), reply_service=reply)
    await ingress.handle(qq_message())
    assert reply.sent[-1] == UNBOUND_REPLY
    bind(db, account_id)
    await ingress.handle(qq_message())
    await ingress.handle(qq_message("2"))
    assert reply.sent[-1] == BUSY_REPLY
    unavailable = MessageIngressService(conversation_service=service("postgresql://no@127.0.0.1:1/no"), reply_service=reply)
    await unavailable.handle(qq_message("3"))
    assert reply.sent[-1] == IDENTITY_RESOLUTION_UNAVAILABLE_REPLY
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


async def test_group_snapshot_context_and_memory_come_from_committed_pg(db, account_id, worker_database_url):
    bind(db, account_id)
    class GroupCache:
        def __init__(self): self.items = []
        async def append(self, **kwargs):
            self.items.append({"msg_id": kwargs["msg_id"], "sender_id": kwargs["sender_id"], "content": kwargs["content"]})
        async def get_window_json(self, key): return list(self.items)
    cache = GroupCache()
    ingress = MessageIngressService(group_context=cache, conversation_service=service(worker_database_url))
    await ingress.handle(qq_message("ambient", "group fact", scope="group", trigger=False))
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    result = await ingress.handle(qq_message("trigger", scope="group"))
    cache.items.clear()
    run_id = UUID(result["run_id"])
    context = ContextAssemblyService(worker_database_url, HarnessContextBuilder())
    base = context.load_base(account_id, run_id)
    assert base.interaction_profile == "qq_group"
    assert "group fact" in base.origin["group_context"] and "qq:" in base.origin["group_context"]
    assert "group fact" in str(context.compose(base, ()))
    assert ChatRunInputLoader(AgentDataStore(worker_database_url)).load(str(run_id)).context.surface == "napcat"
    class Memory:
        async def recall(self, *args, **kwargs): self.recall_args = kwargs; return []
        async def retain_document(self, events, **kwargs): self.events, self.args = events, kwargs; return RetainReceipt(accepted=True)
    memory = Memory()
    await ContextAssemblyService(worker_database_url, HarnessContextBuilder(), memory).recall_long_term(base, "query")
    assert memory.recall_args["group_id"] == base.origin["context_key"]
    commands = CommandService(worker_database_url)
    commands.start_run(account_id, run_id)
    commands.complete_run(account_id, run_id, "persisted reply")
    await MemoryRetentionService(worker_database_url, memory).retain_completed_run(run_id)
    assert memory.args["channel_type"] == "napcat" and memory.args["scope"] == "group"
    assert memory.args["user_id"] == str(account_id)
    assert memory.args["metadata"]["external_message_id"] == "trigger"
    assert memory.args["group_id"] == base.origin["context_key"]
    assert memory.events == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "persisted reply"}]


async def test_distinct_first_deliveries_share_binding_and_pg_admission(db, account_id, worker_database_url):
    bind(db, account_id)
    adapter = service(worker_database_url)
    results = await asyncio.gather(adapter.accept(qq_message("a"), "napcat"),
                                   adapter.accept(qq_message("b"), "napcat"))
    assert len({r["conversation_id"] for r in results}) == 1
    assert sorted(r.response_status for r in results) == [202, 409]
    assert db.execute("SELECT count(*) FROM conversation_bindings").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["callback", "database"])
async def test_official_duplicate_reaches_pg_after_first_failure(
    db, account_id, worker_database_url, failure,
):
    from channels.official_qq import OfficialQQChannel

    bind(db, account_id, channel="official_qq")
    channel = OfficialQQChannel()
    channel._app_id = "test-bot"
    ingress = MessageIngressService(conversation_service=service(worker_database_url))
    unavailable = MessageIngressService(
        conversation_service=service("postgresql://no@127.0.0.1:1/no"),
    )
    calls = []

    async def callback(message):
        calls.append(message)
        if len(calls) == 1:
            if failure == "callback":
                raise RuntimeError("callback failed before admission")
            return await unavailable.handle(message)
        return await ingress.handle(message)

    channel._callback = callback
    raw = {"t": "C2C_MESSAGE_CREATE", "d": {"id": "same-provider-id",
           "content": "hello", "author": {"id": "123"}}}
    await channel._handle_dispatch(raw)
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    await channel._handle_dispatch(raw)
    await channel._handle_dispatch(raw)
    assert len(calls) == 3
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM conversation_ingress_receipts").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM outbox_events WHERE event_type='start_run'").fetchone()[0] == 1


async def test_napcat_image_provenance_is_committed_to_pg(db, account_id, worker_database_url):
    from channels.napcat import NapCatChannel

    bind(db, account_id)
    message = await NapCatChannel().normalize_message({
        "post_type": "message", "message_type": "private", "self_id": 999,
        "message_id": 42, "sender": {"user_id": 123}, "message": [
            {"type": "image", "data": {"url": "https://example.invalid/image.png"}},
            {"type": "image", "data": {"file": "provider-image-reference"}},
        ],
    })
    await MessageIngressService(conversation_service=service(worker_database_url)).handle(message)
    origin = db.execute("SELECT origin FROM messages WHERE role='user'").fetchone()[0]
    assert origin["metadata"]["image_urls"] == [
        "https://example.invalid/image.png", "provider-image-reference",
    ]
