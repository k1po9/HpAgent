from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from application.group_context_selection import SelectionPolicy, render_background, select_context
from common.token_counter import estimate_tokens
from memory.group_context import GroupContextStore


@pytest.fixture
async def group_store():
    import os

    from redis.asyncio import Redis

    url = os.getenv('GROUP_CONTEXT_TEST_REDIS_URL')
    if not url:
        pytest.skip('requires explicit isolated GROUP_CONTEXT_TEST_REDIS_URL')
    client = Redis.from_url(url)
    store = GroupContextStore(client, window_size=3, ttl_seconds=2, seen_limit=6)
    yield store, client, 'test:' + str(uuid4())
    await client.aclose()


async def test_atomic_concurrent_duplicate_conflict_and_order(group_store):
    store, client, key = group_store
    results = await asyncio.gather(*(store.append(key, event_id='one', sender_id='u',
                                                content='same') for _ in range(100)))
    assert sum(r.status == 'appended' for r in results) == 1
    assert sum(r.status == 'duplicate' for r in results) == 99
    assert (await store.append(key, event_id='one', sender_id='u', content='other')).status == 'conflict'
    for i in range(2, 9):
        await store.append(key, event_id=str(i), sender_id='u', content=str(i))
    read = await store.get_candidates(key)
    assert read.status == 'ok'
    assert [m['content'] for m in read.messages] == ['6', '7', '8']
    assert [m['sequence'] for m in read.messages] == [6, 7, 8]
    keys = store.keys(key)
    assert await client.zcard(keys[1]) <= 6
    assert await client.hlen(keys[2]) <= 6
    assert all(0 < ttl <= 2000 for ttl in await asyncio.gather(*(client.pttl(k) for k in keys)))
    await client.pexpire(keys[0], 1)
    # Loss of a candidate list does not erase retained idempotency state.
    await asyncio.sleep(.01)
    assert (await store.append(key, event_id='8', sender_id='u', content='8')).status == 'duplicate'
    for k in keys:
        await client.pexpire(k, 1)
    await asyncio.sleep(.01)
    assert (await store.get_candidates(key)).status == 'empty'
    assert (await store.append(key, event_id='8', sender_id='u', content='8')).status == 'appended'


async def test_bytes_limit_hashes_original_content_and_unknown_commit_retry(group_store):
    store, _, key = group_store
    content = '猫' * 10000
    assert (await store.append(key, event_id='big', sender_id='u', content=content)).status == 'appended'
    assert (await store.append(key, event_id='big', sender_id='u', content=content)).status == 'duplicate'
    assert (await store.append(key, event_id='big', sender_id='u', content=content + 'x')).status == 'conflict'
    message = (await store.get_candidates(key)).messages[0]
    import json
    assert len(json.dumps(message, ensure_ascii=False).encode()) <= 8192
    assert message['truncated']


async def test_store_exposes_failures_and_bounds_slow_redis():
    class Broken:
        async def eval(self, *args):
            raise ConnectionError('down')
        async def lrange(self, *args):
            await asyncio.sleep(1)
    store = GroupContextStore(Broken(), timeout_seconds=.01)
    assert (await store.append('key', event_id='e', sender_id='u', content='c')).status == 'degraded'
    assert (await store.get_candidates('key')).status == 'degraded'


def candidate(i, text='context', **kw):
    return dict(event_id=str(i), sequence=i, sender_id='u', sender_name='Alice',
                content=text, event_time='2026-11-13T01:00:00Z',
                received_at='2026-11-13T01:00:00Z', message_type='user_message',
                thread_id='', reply_to_event_id=None, **kw)


def test_selection_filters_and_bounds_complete_rendered_background():
    now = datetime(2026, 11, 13, 1, 1, tzinfo=UTC)
    items = [candidate(i, '长' * 1000) for i in range(1, 81)]
    items += [dict(candidate(81), message_type='control_reply'),
              dict(candidate(82), event_time=(now + timedelta(minutes=1)).isoformat()),
              dict(candidate(83), event_time=(now - timedelta(hours=2)).isoformat()),
              dict(candidate(84), thread_id='other')]
    snapshot = select_context(items, trigger_event_id='80', sender_id='u', thread_id='', now=now)
    assert snapshot['schema_version'] == 1
    assert snapshot['selected_count'] <= 20
    assert '80' not in [m['event_id'] for m in snapshot['selected']]
    assert all(int(m['event_id']) < 80 for m in snapshot['selected'])
    assert estimate_tokens(render_background(snapshot['selected'])) + 4 <= 4000
    assert all(estimate_tokens(m['content']) <= 512 for m in snapshot['selected'])
    assert [m['sequence'] for m in snapshot['selected']] == sorted(m['sequence'] for m in snapshot['selected'])
    assert snapshot['estimated_tokens'] == estimate_tokens(render_background(snapshot['selected'])) + 4


def test_reply_priority_soft_quota_and_dedup_are_deterministic():
    now = datetime(2026, 11, 13, 1, 1, tzinfo=UTC)
    items = [candidate(1, 'important reply'), candidate(2, 'ok'), candidate(3, 'other topic')]
    snapshot = select_context(items + [items[0]], trigger_event_id='trigger', sender_id='asker',
                              reply_to_event_id='1', now=now, policy=SelectionPolicy(max_messages=1))
    assert [m['event_id'] for m in snapshot['selected']] == ['1']
    assert snapshot == select_context(items + [items[0]], trigger_event_id='trigger', sender_id='asker',
                                     reply_to_event_id='1', now=now, policy=SelectionPolicy(max_messages=1))


async def test_bot_echo_before_accept_enriches_provenance_without_duplicate(group_store):
    store, _, key = group_store
    await store.append(key, event_id='bot-event', sender_id='bot', content='answer')
    result = await store.append(key, event_id='bot-event', sender_id='bot', content='answer',
                                message_type='assistant_final', source_message_id='pg-message',
                                delivery_id='delivery', part_index=0)
    assert result.status == 'duplicate' and result.sequence == 1
    messages = (await store.get_candidates(key)).messages
    assert len(messages) == 1
    assert messages[0]['source_message_id'] == 'pg-message'
    assert messages[0]['message_type'] == 'assistant_final'


async def test_lost_append_response_can_retry_without_duplication(group_store):
    store, client, key = group_store

    class LostResponse:
        async def eval(self, *args):
            await client.eval(*args)
            raise ConnectionError('response lost after commit')
    lost = GroupContextStore(LostResponse())
    assert (await lost.append(key, event_id='e', sender_id='u', content='hi')).status == 'degraded'
    assert (await store.append(key, event_id='e', sender_id='u', content='hi')).status == 'duplicate'
    assert len((await store.get_candidates(key)).messages) == 1


async def test_redis_encoded_json_not_just_python_json_obeys_byte_cap(group_store):
    store, client, key = group_store
    await store.append(key, event_id='slashes', sender_id='u', content='/' * 20000)
    raw = (await client.lrange(store.keys(key)[0], 0, 0))[0]
    assert len(raw) <= 8192
