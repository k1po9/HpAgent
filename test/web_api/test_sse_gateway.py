"""SSE Gateway + Terminal Event Publisher contract tests.

Covers hpagent-web-api-contract.md §12 (SSE contract) and §13 (disconnect and
refetch): handshake ordering, bounded handshake buffer, stream_id changes,
terminal snapshot overrides, duplicate terminal delivery, auth.expired, the
connection limit, the no-Redis degradation, and refresh recovery.

The async tests drive :class:`SSEGateway.stream` directly with a fresh
aioredis client (a separate event loop from the TestClient's portal loop) and
publish through the synchronous ``sync_redis`` fixture, so no async object is
ever shared across two loops.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any
from uuid import UUID, uuid4

import pytest
import redis.asyncio as aioredis
from uuid6 import uuid7

from web_api.sse import SSEGateway

pytestmark = pytest.mark.postgres

_TOPIC_PREFIX = "hpagent:web:run:"
_END = object()

TERMINAL_BY_STATUS = {
    "completed": ("run.completed", "completed"),
    "failed": ("run.failed", "failed"),
    "cancelled": ("run.cancelled", "aborted"),
}


def login(client, username: str = "alice") -> str:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "correct-password", "return_to": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    return str(me.json()["csrf_token"])


def command_headers(csrf: str, key: str | None = None) -> dict[str, str]:
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def create_running_run(client, csrf: str) -> dict[str, str]:
    """Create a conversation + Run and wait until the fake executor marks it running."""
    conversation_id = client.post(
        "/api/v1/conversations",
        json={"title": None},
        headers=command_headers(csrf, str(uuid4())),
    ).json()["conversation"]["conversation_id"]
    sent = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "run it"},
        headers=command_headers(csrf, str(uuid4())),
    ).json()
    run_id = sent["run"]["run_id"]
    for _ in range(100):
        status = client.get(f"/api/v1/runs/{run_id}").json()["run"]["status"]
        if status == "running":
            break
        time.sleep(0.01)
    assert status == "running"
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "message_id": sent["assistant_message"]["message_id"],
    }


def mark_terminal(
    db,
    account_id: UUID,
    conversation_id: str,
    run_id: str,
    status: str,
    content: str | None = None,
) -> None:
    """Commit a terminal Run + assistant Message directly (the DB truth source).

    Both writes happen in one transaction: the deferred run/message invariant
    trigger only checks at commit, so the two rows must change together.
    """
    event_type, message_status = TERMINAL_BY_STATUS[status]
    with db.transaction():
        if message_status == "completed":
            db.execute(
                "UPDATE messages SET status='completed', content=%s, completed_at=now() "
                "WHERE produced_by_run_id=%s AND role='assistant'",
                (content, run_id),
            )
        else:
            db.execute(
                "UPDATE messages SET status=%s, completed_at=now() "
                "WHERE produced_by_run_id=%s AND role='assistant'",
                (message_status, run_id),
            )
        if status == "failed":
            db.execute(
                "UPDATE runs SET status='failed', failure_code='model_unavailable', "
                "failure_message='Agent 暂时无法完成本次请求，请稍后重试。', finished_at=now(), "
                "version=version+1, updated_at=now() WHERE run_id=%s",
                (run_id,),
            )
        else:
            db.execute(
                "UPDATE runs SET status=%s, finished_at=now(), version=version+1, "
                "updated_at=now() WHERE run_id=%s",
                (status, run_id),
            )
    assert event_type


def enqueue_terminal(
    db,
    account_id: UUID,
    conversation_id: str,
    run_id: str,
    status: str,
) -> str:
    """Insert a pending ``publish_terminal_event`` Outbox row the publisher will claim."""
    terminal_event_id = str(uuid7())
    db.execute(
        "INSERT INTO outbox_events(outbox_event_id, account_id, event_type, business_key, "
        "conversation_id, run_id, payload_version, payload) "
        "VALUES (%s,%s,'publish_terminal_event',%s,%s,%s,1,%s::jsonb)",
        (
            uuid4(),
            account_id,
            f"terminal:{run_id}:{status}",
            conversation_id,
            run_id,
            json.dumps(
                {
                    "run_id": run_id,
                    "terminal_status": status,
                    "terminal_event_id": terminal_event_id,
                    "version": 1,
                }
            ),
        ),
    )
    return terminal_event_id


def make_online_event(
    run_id: str,
    *,
    event_type: str,
    stream_id: str,
    seq: int,
    message_id: str | None = None,
    payload: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "event_id": str(uuid7()),
            "event_type": event_type,
            "run_id": run_id,
            "message_id": message_id,
            "stream_id": stream_id,
            "event_seq": seq,
            "occurred_at": "2026-08-07T00:00:00.000Z",
            "payload": payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def make_terminal_event(
    *,
    run_id: str,
    conversation_id: str,
    event_id: str,
) -> str:
    """A manually published terminal envelope (the publisher's exact shape)."""
    return json.dumps(
        {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": "run.completed",
            "conversation_id": conversation_id,
            "run_id": run_id,
            "message_id": None,
            "stream_id": None,
            "event_seq": None,
            "occurred_at": "2026-08-07T00:00:00.000Z",
            "payload": {"snapshot": {}},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_sse_frame(frame: str) -> dict[str, Any] | None:
    """Parse one SSE frame; ``None`` for keepalive comment frames."""
    event_type = ""
    data_lines: list[str] = []
    for line in frame.splitlines():
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if not data_lines:
        return None
    envelope = json.loads("\n".join(data_lines))
    envelope["event_type"] = event_type or envelope.get("event_type")
    return envelope


def collect_sse(response) -> list[dict[str, Any]]:
    """Read an already-terminating SSE response body into parsed frames."""
    frames: list[dict[str, Any]] = []
    current: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if current:
                parsed = parse_sse_frame("\n".join(current))
                if parsed is not None:
                    frames.append(parsed)
                current = []
        else:
            current.append(line)
    return frames


class StreamReader:
    """Consume an async SSE generator and expose parsed frames as an asyncio.Queue."""

    def __init__(self, agen):
        self.agen = agen
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        try:
            async for frame in self.agen:
                parsed = parse_sse_frame(frame)
                if parsed is None:
                    continue
                self.queue.put_nowait(parsed)
        except Exception:
            pass
        finally:
            self.queue.put_nowait(_END)

    async def next(self, timeout: float = 5.0) -> Any:
        return await asyncio.wait_for(self.queue.get(), timeout)

    async def expect(self, event_type: str, timeout: float = 5.0) -> dict[str, Any]:
        while True:
            item = await self.next(timeout)
            if item is _END:
                raise AssertionError(f"stream ended before {event_type}")
            if item["event_type"] == event_type:
                return item

    async def drain(self, timeout: float = 5.0) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        while True:
            item = await self.next(timeout)
            if item is _END:
                return frames
            frames.append(item)

    async def close(self) -> None:
        if not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task


async def make_gateway(client, redis_url: str) -> tuple[SSEGateway, aioredis.Redis]:
    """A gateway over the app's pool/settings with a loop-local Redis client."""
    redis_client = aioredis.from_url(redis_url, decode_responses=False)
    gateway = SSEGateway(
        client.app.state.api_pool, client.app.state.settings, redis_client
    )
    return gateway, redis_client


def wait_until_subscribed(sync_redis, channel: str, timeout: float = 5.0) -> None:
    """Wait until the gateway's pubsub subscription to ``channel`` is live.

    The Gateway yields ``run.snapshot`` *before* its Redis ``SUBSCRIBE``
    completes, and Redis pubsub drops messages published before a SUBSCRIBE is
    acknowledged. Tests that publish online deltas must not race that window;
    once ``PUBSUB NUMSUB`` counts a subscriber the SUBSCRIBE has completed, so
    subsequent publishes are guaranteed to arrive.
    """
    deadline = time.monotonic() + timeout
    channel_bytes = channel.encode()
    while time.monotonic() < deadline:
        numsub = sync_redis.pubsub_numsub(channel)
        for sub_channel, count in numsub:
            if sub_channel == channel_bytes and count > 0:
                return
        time.sleep(0.01)
    raise AssertionError(f"gateway never subscribed to {channel}")


# --------------------------------------------------------------------------- #
# HTTP level: headers, ownership, connection limit, no-Redis degradation.
# --------------------------------------------------------------------------- #

def test_sse_http_terminal_snapshot_refresh_recovery(
    client_factory, seed_identity, db, redis_url
):
    """API-012/API-014: refresh recovery — a reconnected stream opens on the
    committed terminal snapshot and closes immediately (no live deltas)."""
    account_id = seed_identity("alice")
    client = client_factory(redis_url=redis_url, fake_enabled=True, fake_mode="hold")
    csrf = login(client)
    run = create_running_run(client, csrf)
    mark_terminal(db, account_id, run["conversation_id"], run["run_id"], "completed", "terminal content")
    with client.stream("GET", f"/api/v1/runs/{run['run_id']}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in response.headers["cache-control"]
        assert response.headers["x-accel-buffering"] == "no"
        frames = collect_sse(response)
    assert [frame["event_type"] for frame in frames] == ["run.snapshot"]
    snapshot = frames[0]["payload"]["snapshot"]
    assert snapshot["run"]["status"] == "completed"
    assert snapshot["assistant_message"]["content"] == "terminal content"
    assert frames[0]["stream_id"] is None
    assert frames[0]["event_seq"] is None


def test_sse_http_redis_unavailable_degrades_to_poll(
    client_factory, seed_identity
):
    """No Redis: snapshot first, then stream.degraded(redis_unavailable); the
    Run stays pollable (API-013)."""
    seed_identity("alice")
    client = client_factory(fake_enabled=True, fake_mode="hold")  # no redis_url
    csrf = login(client)
    run = create_running_run(client, csrf)
    with client.stream("GET", f"/api/v1/runs/{run['run_id']}/events") as response:
        assert response.status_code == 200
        frames = collect_sse(response)
    assert [frame["event_type"] for frame in frames] == [
        "run.snapshot",
        "stream.degraded",
    ]
    degraded = frames[1]["payload"]
    assert degraded["reason"] == "redis_unavailable"
    assert degraded["recovery"] == "query_run"
    assert degraded["retry_after_ms"] == 2000
    # The client's fallback (polling the Run) still works.
    assert client.get(f"/api/v1/runs/{run['run_id']}").status_code == 200


def test_sse_http_fake_executor_streams_online_events_then_terminal(
    seed_identity, client_factory, redis_url
):
    """Phase-e exit gate: with Redis the Fake Run Executor projects the contract
    online events (run.started / run.progress / message.delta) under one
    stream_id before the committed run.completed snapshot closes the stream."""
    seed_identity("alice")
    client = client_factory(
        redis_url=redis_url, fake_enabled=True, fake_mode="success", fake_delay=0.05
    )
    csrf = login(client)
    conversation_id = client.post(
        "/api/v1/conversations",
        json={"title": None},
        headers=command_headers(csrf, str(uuid4())),
    ).json()["conversation"]["conversation_id"]
    sent = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "stream please"},
        headers=command_headers(csrf, str(uuid4())),
    ).json()
    run_id = sent["run"]["run_id"]
    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as response:
        assert response.status_code == 200
        frames = collect_sse(response)

    types = [frame["event_type"] for frame in frames]
    assert types[0] == "run.snapshot"
    assert "run.started" in types
    assert any(t == "run.progress" for t in types)
    assert any(t == "message.delta" for t in types)
    assert types[-1] == "run.completed"

    # The online events share one stream_id and a monotonic event_seq.
    online = [
        frame
        for frame in frames
        if frame["event_type"] in ("run.started", "run.progress", "message.delta")
    ]
    assert online, "online events must reach the stream before the terminal"
    assert len({frame["stream_id"] for frame in online}) == 1
    seqs = [frame["event_seq"] for frame in online]
    assert all(seq is not None for seq in seqs)
    assert seqs == sorted(seqs)

    # The delta carries the pending assistant Message id; the terminal snapshot
    # is authoritative with null stream_id/event_seq.
    delta = next(frame for frame in frames if frame["event_type"] == "message.delta")
    assert delta["message_id"] == sent["assistant_message"]["message_id"]
    terminal = frames[-1]
    assert terminal["stream_id"] is None
    assert terminal["event_seq"] is None
    assert (
        terminal["payload"]["snapshot"]["assistant_message"]["content"]
        == "fake completed response"
    )


def test_sse_connection_limit_returns_503(client_factory, seed_identity, redis_url):
    """A bounded number of concurrent SSE connections: the excess gets a JSON 503.

    The slot is occupied on the app's own gateway through the TestClient portal
    (the same event loop the app runs on). TestClient cannot hold a live,
    never-ending SSE stream open while issuing a second request: its transport
    blocks until the ASGI call returns, which for an infinite stream never
    happens. Occupying the slot directly exercises the endpoint's acquire gate
    without a streaming request body.
    """
    seed_identity("alice")
    client = client_factory(
        redis_url=redis_url,
        sse_max_connections=1,
        fake_enabled=True,
        fake_mode="hold",
    )
    csrf = login(client)
    run = create_running_run(client, csrf)
    gateway = client.app.state.sse_gateway
    assert client.portal.call(gateway.try_acquire) is True
    assert gateway.active_connections == 1
    second = client.get(f"/api/v1/runs/{run['run_id']}/events")
    assert second.status_code == 503
    assert second.json()["error"]["code"] == "service_unavailable"
    # The 503 path must not leak the slot: releasing frees it for the next caller.
    client.portal.call(gateway.release)
    assert gateway.active_connections == 0
    assert client.portal.call(gateway.try_acquire) is True
    client.portal.call(gateway.release)


# --------------------------------------------------------------------------- #
# Streaming contract (drives SSEGateway.stream directly).
# --------------------------------------------------------------------------- #

async def test_sse_snapshot_and_handshake_ordering(
    client_factory, seed_identity, redis_url, sync_redis
):
    """API-010: run.snapshot arrives first, then online deltas are drained in
    event_seq order under a single stream_id; each delta carries the run's
    message_id and a monotonic sequence."""
    account_id = seed_identity("alice")
    client = client_factory(redis_url=redis_url, fake_enabled=True, fake_mode="hold")
    csrf = login(client)
    run = create_running_run(client, csrf)
    gateway, redis_client = await make_gateway(client, redis_url)
    reader = StreamReader(
        gateway.stream(account_id, UUID(run["run_id"]), "", acquired=True)
    )
    try:
        snapshot = await reader.expect("run.snapshot")
        assert snapshot["payload"]["snapshot"]["run"]["status"] == "running"
        assert snapshot["payload"]["snapshot"]["assistant_message"]["status"] == "pending"
        assert snapshot["stream_id"] is None and snapshot["event_seq"] is None

        stream_id = str(uuid7())
        channel = f"{_TOPIC_PREFIX}{run['run_id']}"
        await asyncio.to_thread(wait_until_subscribed, sync_redis, channel)
        await asyncio.to_thread(
            sync_redis.publish, channel,
            make_online_event(
                run["run_id"], event_type="message.delta", stream_id=stream_id,
                seq=1, message_id=run["message_id"], payload={"delta": "你"},
            ),
        )
        first = await reader.expect("message.delta")
        assert first["stream_id"] == stream_id
        assert first["event_seq"] == 1
        assert first["message_id"] == run["message_id"]
        assert first["payload"] == {"delta": "你"}

        await asyncio.to_thread(
            sync_redis.publish, channel,
            make_online_event(
                run["run_id"], event_type="message.delta", stream_id=stream_id,
                seq=2, message_id=run["message_id"], payload={"delta": "好"},
            ),
        )
        second = await reader.expect("message.delta")
        assert second["event_seq"] == 2
        assert second["payload"] == {"delta": "好"}

        await asyncio.to_thread(
            sync_redis.publish, channel,
            make_online_event(
                run["run_id"], event_type="run.progress", stream_id=stream_id,
                seq=3, payload={"phase": "executing_tool", "summary": "正在执行工具"},
            ),
        )
        progress = await reader.expect("run.progress")
        assert progress["event_seq"] == 3
        assert progress["payload"]["phase"] == "executing_tool"
    finally:
        await reader.close()
        await redis_client.aclose()


async def test_sse_stream_id_change_degrades_sequence_gap(
    client_factory, seed_identity, redis_url, sync_redis
):
    """API-011/API-020: a second stream_id (a Worker/Event Sink restart) breaks
    the online continuity and the stream degrades with sequence_gap."""
    account_id = seed_identity("alice")
    client = client_factory(redis_url=redis_url, fake_enabled=True, fake_mode="hold")
    csrf = login(client)
    run = create_running_run(client, csrf)
    gateway, redis_client = await make_gateway(client, redis_url)
    reader = StreamReader(
        gateway.stream(account_id, UUID(run["run_id"]), "", acquired=True)
    )
    try:
        await reader.expect("run.snapshot")
        channel = f"{_TOPIC_PREFIX}{run['run_id']}"
        await asyncio.to_thread(wait_until_subscribed, sync_redis, channel)
        await asyncio.to_thread(
            sync_redis.publish, channel,
            make_online_event(
                run["run_id"], event_type="message.delta", stream_id=str(uuid7()),
                seq=1, message_id=run["message_id"], payload={"delta": "a"},
            ),
        )
        await reader.expect("message.delta")
        # New stream_id => different online lifecycle; the gap cannot be filled.
        await asyncio.to_thread(
            sync_redis.publish, channel,
            make_online_event(
                run["run_id"], event_type="message.delta", stream_id=str(uuid7()),
                seq=1, message_id=run["message_id"], payload={"delta": "b"},
            ),
        )
        degraded = await reader.expect("stream.degraded")
        assert degraded["payload"]["reason"] == "sequence_gap"
        assert await reader.next() is _END
    finally:
        await reader.close()
        await redis_client.aclose()


async def test_sse_handshake_buffer_overflow_degrades(
    client_factory, seed_identity, redis_url, sync_redis
):
    """API-011: a byte budget smaller than the burst of deltas degrades with
    handshake_buffer_overflow instead of silently dropping delta content."""
    account_id = seed_identity("alice")
    client = client_factory(
        redis_url=redis_url,
        sse_handshake_buffer_bytes=300,
        fake_enabled=True,
        fake_mode="hold",
    )
    csrf = login(client)
    run = create_running_run(client, csrf)
    gateway, redis_client = await make_gateway(client, redis_url)
    reader = StreamReader(
        gateway.stream(account_id, UUID(run["run_id"]), "", acquired=True)
    )
    try:
        await reader.expect("run.snapshot")
        channel = f"{_TOPIC_PREFIX}{run['run_id']}"
        await asyncio.to_thread(wait_until_subscribed, sync_redis, channel)
        stream_id = str(uuid7())
        for seq in range(1, 6):
            await asyncio.to_thread(
                sync_redis.publish, channel,
                make_online_event(
                    run["run_id"], event_type="message.delta", stream_id=stream_id,
                    seq=seq, message_id=run["message_id"],
                    payload={"delta": "x" * 120},
                ),
            )
        degraded = await reader.expect("stream.degraded")
        assert degraded["payload"]["reason"] == "handshake_buffer_overflow"
        assert await reader.next() is _END
    finally:
        await reader.close()
        await redis_client.aclose()


@pytest.mark.parametrize(
    "status,terminal_event,message_status",
    [
        ("completed", "run.completed", "completed"),
        ("failed", "run.failed", "failed"),
        ("cancelled", "run.cancelled", "aborted"),
    ],
)
async def test_sse_terminal_via_publisher(
    client_factory, seed_identity, redis_url, sync_redis, db,
    status, terminal_event, message_status,
):
    """API-012: the Outbox-driven Terminal Event Publisher turns a committed
    terminal fact into run.completed/failed/cancelled carrying the full DB
    snapshot; the Gateway confirms against the DB and closes the stream."""
    account_id = seed_identity("alice")
    client = client_factory(redis_url=redis_url, fake_enabled=True, fake_mode="hold")
    csrf = login(client)
    run = create_running_run(client, csrf)
    gateway, redis_client = await make_gateway(client, redis_url)
    reader = StreamReader(
        gateway.stream(account_id, UUID(run["run_id"]), "", acquired=True)
    )
    try:
        await reader.expect("run.snapshot")
        content = "terminal final reply" if status == "completed" else None
        mark_terminal(
            db, account_id, run["conversation_id"],
            run["run_id"], status, content,
        )
        enqueue_terminal(
            db, account_id, run["conversation_id"],
            run["run_id"], status,
        )
        terminal = await reader.expect(terminal_event, timeout=10.0)
        assert terminal["stream_id"] is None
        assert terminal["event_seq"] is None
        snapshot = terminal["payload"]["snapshot"]
        assert snapshot["run"]["status"] == status
        assert snapshot["assistant_message"]["status"] == message_status
        assert snapshot["assistant_message"]["message_id"] == run["message_id"]
        if content is not None:
            assert snapshot["assistant_message"]["content"] == content
        if status == "failed":
            assert snapshot["run"]["failure"]["code"] == "model_unavailable"
        assert await reader.next() is _END
    finally:
        await reader.close()
        await redis_client.aclose()


async def test_sse_terminal_snapshot_overrides_deltas(
    client_factory, seed_identity, redis_url, sync_redis, db
):
    """API-023: buffered deltas are superseded by the terminal DB snapshot, so
    the final assistant content comes from the committed Message, not from
    whatever deltas happened to be streamed."""
    account_id = seed_identity("alice")
    client = client_factory(redis_url=redis_url, fake_enabled=True, fake_mode="hold")
    csrf = login(client)
    run = create_running_run(client, csrf)
    gateway, redis_client = await make_gateway(client, redis_url)
    reader = StreamReader(
        gateway.stream(account_id, UUID(run["run_id"]), "", acquired=True)
    )
    try:
        await reader.expect("run.snapshot")
        channel = f"{_TOPIC_PREFIX}{run['run_id']}"
        await asyncio.to_thread(wait_until_subscribed, sync_redis, channel)
        stream_id = str(uuid7())
        await asyncio.to_thread(
            sync_redis.publish, channel,
            make_online_event(
                run["run_id"], event_type="message.delta", stream_id=stream_id,
                seq=1, message_id=run["message_id"], payload={"delta": "partial "},
            ),
        )
        await reader.expect("message.delta")
        mark_terminal(
            db, account_id, run["conversation_id"],
            run["run_id"], "completed", "committed complete reply",
        )
        enqueue_terminal(
            db, account_id, run["conversation_id"],
            run["run_id"], "completed",
        )
        terminal = await reader.expect("run.completed", timeout=10.0)
        assert terminal["payload"]["snapshot"]["assistant_message"]["content"] == (
            "committed complete reply"
        )
        assert terminal["payload"]["snapshot"]["assistant_message"]["status"] == "completed"
        assert await reader.next() is _END
    finally:
        await reader.close()
        await redis_client.aclose()


async def test_sse_terminal_duplicate_delivery_is_idempotent(
    client_factory, seed_identity, redis_url, sync_redis, db
):
    """API-012: duplicate terminal delivery (at-least-once) must not produce a
    duplicate run.completed — the Gateway closes on the first terminal event and
    ignores anything already buffered."""
    account_id = seed_identity("alice")
    client = client_factory(redis_url=redis_url, fake_enabled=True, fake_mode="hold")
    csrf = login(client)
    run = create_running_run(client, csrf)
    gateway, redis_client = await make_gateway(client, redis_url)
    reader = StreamReader(
        gateway.stream(account_id, UUID(run["run_id"]), "", acquired=True)
    )
    try:
        await reader.expect("run.snapshot")
        mark_terminal(
            db, account_id, run["conversation_id"],
            run["run_id"], "completed", "idempotent reply",
        )
        channel = f"{_TOPIC_PREFIX}{run['run_id']}"
        await asyncio.to_thread(wait_until_subscribed, sync_redis, channel)
        same_event_id = str(uuid7())
        frame = make_terminal_event(
            run_id=run["run_id"], conversation_id=run["conversation_id"],
            event_id=same_event_id,
        )
        await asyncio.to_thread(sync_redis.publish, channel, frame)
        await asyncio.to_thread(sync_redis.publish, channel, frame)
        terminal = await reader.expect("run.completed")
        assert terminal["event_id"] == same_event_id
        # The stream is already closed: the duplicate is never processed.
        assert await reader.next() is _END
    finally:
        await reader.close()
        await redis_client.aclose()


async def test_sse_auth_expired_on_keepalive(
    client_factory, seed_identity, redis_url
):
    """API-015: when the session stops authenticating, the keepalive tick emits
    auth.expired with an empty payload and closes the stream."""
    account_id = seed_identity("alice")
    client = client_factory(
        redis_url=redis_url,
        sse_keepalive_seconds=0.2,
        fake_enabled=True,
        fake_mode="hold",
    )
    csrf = login(client)
    run = create_running_run(client, csrf)
    gateway, redis_client = await make_gateway(client, redis_url)
    reader = StreamReader(
        gateway.stream(
            account_id,
            UUID(run["run_id"]),
            "some-cookie-value",
            lambda raw: False,
            acquired=True,
        )
    )
    try:
        await reader.expect("run.snapshot")
        expired = await reader.expect("auth.expired", timeout=5.0)
        assert expired["payload"] == {}
        assert expired["stream_id"] is None
        assert await reader.next() is _END
    finally:
        await reader.close()
        await redis_client.aclose()

