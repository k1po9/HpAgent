"""Best-effort Redis event projection for one Web Run; never publishes terminal state."""
from __future__ import annotations

import json
from typing import Protocol
from uuid import uuid4

_PROGRESS_PHASES = frozenset(
    {"starting", "selecting_tools", "generating", "executing_tool"}
)


class AsyncRedisPublisher(Protocol):
    async def publish(self, topic: str, payload: str) -> object: ...


class RedisWebRunEventSinkFactory:
    def __init__(self, redis_client: AsyncRedisPublisher | None):
        self._redis = redis_client

    def for_run(self, run_id: str) -> "RedisWebRunEventSink":
        return RedisWebRunEventSink(self._redis, run_id)


class RedisWebRunEventSink:
    def __init__(self, redis_client: AsyncRedisPublisher | None, run_id: str):
        self._redis = redis_client
        self._run_id = run_id
        self._stream_id = str(uuid4())
        self._event_seq = 0
        self.degraded = redis_client is None
        self.closed = False

    async def progress(self, phase: str, summary: str) -> None:
        if phase not in _PROGRESS_PHASES:
            raise ValueError(f"unsupported Web progress phase: {phase}")
        if self.closed or self._redis is None or self.degraded:
            return
        self._event_seq += 1
        payload = {
            "schema_version": 1,
            "type": "run.progress",
            "run_id": self._run_id,
            "stream_id": self._stream_id,
            "event_seq": self._event_seq,
            "phase": phase,
            "summary": summary[:200],
        }
        try:
            await self._redis.publish(
                f"hpagent:web:run:{self._run_id}",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            # Redis is an online projection only.  The Agent execution and
            # authoritative database terminal transaction must continue.
            self.degraded = True

    async def close(self) -> None:
        """Fence late model/tool callbacks from publishing after Host exit."""
        self.closed = True
