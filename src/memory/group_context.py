"""Best-effort, bounded Redis candidates. Business state remains in PostgreSQL."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from memory.group_context_text import DEFAULT_MESSAGE_TOKENS, excerpt

logger = logging.getLogger('HpAgent.GroupContext')

# All keys share a Redis Cluster hash tag. Seen IDs outlive list trimming, but
# are bounded by both a retention horizon and a cardinality cap.
_APPEND = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now - tonumber(ARGV[5]))
for _, id in ipairs(expired) do redis.call('HDEL', KEYS[3], id) end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - tonumber(ARGV[5]))
local old = redis.call('HGET', KEYS[3], ARGV[1])
if old then
    local digest, original_seq = string.match(old, '^([0-9a-f]+)|(%d+)$')
    digest = digest or old -- V2 digest-only records remain readable.
    if digest ~= ARGV[2] then return {'conflict', '0'} end
    local incoming = cjson.decode(ARGV[3])
    local messages = redis.call('LRANGE', KEYS[1], 0, tonumber(ARGV[4]) - 1)
    for i, raw in ipairs(messages) do
        local message = cjson.decode(raw)
        if message.event_id == ARGV[1] then
            -- An echo can arrive before the accepted callback. Enrich only
            -- provenance; never add a second record or change original order.
            if incoming.delivery_id ~= cjson.null then
                message.delivery_id = incoming.delivery_id
                message.source_message_id = incoming.source_message_id
                message.part_index = incoming.part_index
                message.message_type = incoming.message_type
                redis.call('LSET', KEYS[1], i - 1, cjson.encode(message))
            end
            redis.call('HSET', KEYS[3], ARGV[1], ARGV[2] .. '|' .. tostring(message.sequence))
            return {'duplicate', tostring(message.sequence)}
        end
    end
    return {'duplicate', original_seq or '0'}
end
local seq = redis.call('INCR', KEYS[4])
local message = cjson.decode(ARGV[3])
message.sequence = seq
redis.call('LPUSH', KEYS[1], cjson.encode(message))
redis.call('LTRIM', KEYS[1], 0, tonumber(ARGV[4]) - 1)
redis.call('ZADD', KEYS[2], now, ARGV[1])
redis.call('HSET', KEYS[3], ARGV[1], ARGV[2] .. '|' .. tostring(seq))
local excess = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[6])
if excess > 0 then
    local removed = redis.call('ZRANGE', KEYS[2], 0, excess - 1)
    for _, id in ipairs(removed) do
        redis.call('ZREM', KEYS[2], id)
        redis.call('HDEL', KEYS[3], id)
    end
end
for _, key in ipairs(KEYS) do redis.call('EXPIRE', key, ARGV[5]) end
return {'appended', tostring(seq)}
"""


@dataclass(frozen=True)
class AppendResult:
    status: str
    sequence: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class CandidateResult:
    status: str
    messages: tuple[dict[str, Any], ...] = ()
    reason: str | None = None


class GroupContextStore:
    def __init__(self, redis: Any, *, window_size: int = 80, ttl_seconds: int = 86400,
                 max_message_bytes: int = 8192, timeout_seconds: float = .25,
                 seen_limit: int = 4096, message_tokens: int = DEFAULT_MESSAGE_TOKENS):
        if min(window_size, ttl_seconds, seen_limit, message_tokens) <= 0 or max_message_bytes < 1024 or timeout_seconds <= 0:
            raise ValueError('invalid group context limits')
        self._message_tokens = message_tokens
        self._r = redis
        self._window = window_size
        self._ttl = ttl_seconds
        self._bytes = max_message_bytes
        self._timeout = timeout_seconds
        self._seen_limit = max(seen_limit, window_size)

    @staticmethod
    def keys(context_key: str) -> tuple[str, str, str, str]:
        # V2 namespace deliberately leaves legacy plain-text lists to expire.
        tag = hashlib.sha256(context_key.encode()).hexdigest()
        prefix = f'group:context:v2:{{{tag}}}'
        return tuple(f'{prefix}:{suffix}' for suffix in ('messages', 'seen', 'event_hashes', 'sequence'))

    async def append(self, context_key: str, *, event_id: str, sender_id: str, content: str,
                     sender_name: str = '', message_type: str = 'user_message',
                     event_time: str = '', reply_to_event_id: str | None = None,
                     thread_id: str = '', source_message_id: str | None = None,
                     delivery_id: str | None = None, part_index: int | None = None,
                     mention_ids: list[str] | None = None) -> AppendResult:
        if message_type not in {'user_message', 'assistant_final'} or not content.strip():
            return AppendResult('ignored')
        now = datetime.now(UTC).isoformat()
        message = dict(event_id=event_id, sequence=0, sender_id=sender_id,
                       sender_name=sender_name[:128], message_type=message_type,
                       content=content, event_time=event_time or now, received_at=now,
                       reply_to_event_id=reply_to_event_id, thread_id=thread_id,
                       source_message_id=source_message_id, delivery_id=delivery_id,
                       part_index=part_index, truncated=False, mention_ids=(mention_ids or [])[:32])
        # Hash ORIGINAL bytes, not the bounded excerpt. Echoes may add delivery
        # metadata or differ in display name/type; identity and text stay stable.
        digest = hashlib.sha256(json.dumps([sender_id, thread_id, content],
                                          ensure_ascii=False).encode()).hexdigest()
        try:
            if not context_key or not event_id:
                return AppendResult('degraded', reason='missing_identity')
            # One QQ-only prefix function serves both storage and selection.
            # Byte headroom covers sequence growth / later provenance enrichment.
            message['content'] = ''
            overhead = len(json.dumps(message, ensure_ascii=False).replace('/', '\\/').encode())
            message['content'] = excerpt(content, self._message_tokens,
                                         max_bytes=self._bytes - 256 - overhead, already_truncated=False)
            message['truncated'] = message['content'] != content
            if not message['content']:
                return AppendResult('degraded', reason='message_limits_too_small')
            async with asyncio.timeout(self._timeout):
                result = await self._r.eval(_APPEND, 4, *self.keys(context_key), event_id,
                                            digest, json.dumps(message, ensure_ascii=False),
                                            self._window, self._ttl, self._seen_limit)
            status = result[0].decode() if isinstance(result[0], bytes) else result[0]
            if status == 'conflict':
                logger.warning('qq_group_context_conflict context_key=%s', context_key)
            return AppendResult(status, int(result[1]))
        except Exception as exc:
            reason = type(exc).__name__
            logger.warning('qq_group_context_append_degraded reason=%s', reason)
            return AppendResult('degraded', reason=reason)

    async def get_candidates(self, context_key: str) -> CandidateResult:
        try:
            async with asyncio.timeout(self._timeout):
                raw = await self._r.lrange(self.keys(context_key)[0], 0, self._window - 1)
            messages = tuple(json.loads(item) for item in reversed(raw))
            if any(not isinstance(m, dict) or not isinstance(m.get('sequence'), int)
                   or not isinstance(m.get('content'), str) or not m.get('event_id') for m in messages):
                raise ValueError('invalid candidate')
            return CandidateResult('ok' if messages else 'empty', messages)
        except Exception as exc:
            reason = type(exc).__name__
            logger.warning('qq_group_context_read_degraded reason=%s', reason)
            return CandidateResult('degraded', reason=reason)
