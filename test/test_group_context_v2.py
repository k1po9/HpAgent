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


@pytest.mark.parametrize('content', ['普通中文🙂消息', 'text without clipping'])
def test_qq_excerpt_normal_messages_are_unchanged(content):
    from application.group_context_selection import excerpt
    assert excerpt(content, 512) == content


def test_qq_excerpt_prefix_suffix_unicode_and_idempotence():
    from application.group_context_selection import excerpt
    text = '中文🙂🚀代码' * 1000
    result = excerpt(text, 512)
    assert result.endswith(' ...')
    assert text.startswith(result[:-4])
    assert estimate_tokens(result) <= 512
    assert result.encode('utf-8').decode('utf-8') == result
    assert excerpt(result, 512) == result
    smaller = excerpt(result, 100)
    assert smaller.endswith(' ...') and not smaller.endswith(' ... ...')


async def test_qq_store_protects_prefix_before_selection(group_store):
    store, _, key = group_store
    text = '中文🙂代码' * 5000
    await store.append(key, event_id='long', sender_id='u', content=text)
    message = (await store.get_candidates(key)).messages[0]
    assert message['truncated'] is True
    assert message['content'].endswith(' ...')
    assert text.startswith(message['content'][:-4])
    assert estimate_tokens(message['content']) <= 512
    assert message['content'] != text
    await store.append(key, event_id='normal', sender_id='u', content='normal message')
    normal = (await store.get_candidates(key)).messages[-1]
    assert normal['content'] == 'normal message' and normal['truncated'] is False


async def test_duplicate_after_trim_retains_sequence_and_watermark(group_store):
    store, client, key = group_store
    first = await store.append(key, event_id='trigger', sender_id='u', content='trigger')
    for i in range(4):
        await store.append(key, event_id=str(i), sender_id='u', content='later')
    before = await client.lrange(store.keys(key)[0], 0, -1)
    duplicate = await store.append(key, event_id='trigger', sender_id='u', content='trigger')
    assert duplicate.status == 'duplicate'
    assert duplicate.sequence == first.sequence == 1
    assert await client.lrange(store.keys(key)[0], 0, -1) == before
    assert int(await client.get(store.keys(key)[3])) == 5
    snapshot = select_context((await store.get_candidates(key)).messages,
                              trigger_event_id='trigger', sender_id='u', now=datetime.now(UTC),
                              high_watermark=duplicate.sequence)
    assert snapshot['high_watermark'] == 1 and snapshot['selected'] == []


@pytest.mark.parametrize('message_type', ['work_notification', 'system_error', 'progress', 'control_reply'])
async def test_internal_notifications_do_not_consume_window(group_store, message_type):
    store, client, key = group_store
    await store.append(key, event_id='user', sender_id='u', content='question')
    for i in range(5):
        result = await store.append(key, event_id=str(i), sender_id='bot', content='internal',
                                    message_type=message_type)
        assert result.status == 'ignored'
    await store.append(key, event_id='bot', sender_id='bot', content='answer', message_type='assistant_final')
    assert [m['event_id'] for m in (await store.get_candidates(key)).messages] == ['user', 'bot']
    assert int(await client.get(store.keys(key)[3])) == 2
    assert await client.zcard(store.keys(key)[1]) == 2


def test_render_preserves_truncation_time_and_explicit_reply_identity():
    import json
    item = dict(candidate(1, 'prefix ...'), truncated=True,
                reply_to_event_id='parent', mention_ids=['other'])
    rendered = render_background([item])
    value = json.loads(rendered.split('\n')[1])
    assert value['truncated'] is True
    assert value['event_id'] == '1' and value['reply_to_event_id'] == 'parent'
    assert value['event_time'] == item['event_time']
    assert value['sender_id'] == 'u' and value['mention_ids'] == ['other']


async def test_legacy_digest_only_record_keeps_order_or_reports_unknown(group_store):
    store, client, key = group_store
    await store.append(key, event_id='legacy', sender_id='u', content='old')
    hashes = store.keys(key)[2]
    digest = (await client.hget(hashes, 'legacy')).decode().split('|')[0]
    await client.hset(hashes, 'legacy', digest)
    assert (await store.append(key, event_id='legacy', sender_id='u', content='old')).sequence == 1
    # An old hash whose original list entry is gone must not invent a new seq.
    await client.hset(hashes, 'legacy', digest)
    for i in range(3):
        await store.append(key, event_id=str(i), sender_id='u', content='new')
    duplicate = await store.append(key, event_id='legacy', sender_id='u', content='old')
    assert duplicate.status == 'duplicate' and duplicate.sequence == 0
    assert int(await client.get(store.keys(key)[3])) == 4


async def test_json_safety_limit_and_token_limit_use_same_prefix_function(group_store):
    _, client, key = group_store
    store = GroupContextStore(client, max_message_bytes=1024)
    original = '/' * 10000
    await store.append(key, event_id='bounded', sender_id='u', content=original)
    message = (await store.get_candidates(key)).messages[0]
    assert message['truncated'] and message['content'].endswith(' ...')
    assert original.startswith(message['content'][:-4])
    assert len((await client.lrange(store.keys(key)[0], 0, 0))[0]) <= 1024
    assert estimate_tokens(message['content']) <= 512


async def test_redis_empty_mention_list_renders_as_array(group_store):
    import json
    store, _, key = group_store
    await store.append(key, event_id='e', sender_id='u', content='normal')
    item = (await store.get_candidates(key)).messages[0]
    rendered = json.loads(render_background([item]).split('\n')[1])
    assert rendered['mention_ids'] == []


@pytest.mark.parametrize('budget,max_bytes', [(512, None), (5, None), (512, 12), (1, 1)])
def test_excerpt_raw_natural_suffix_uses_explicit_state_and_longest_prefix(budget, max_bytes):
    from memory.group_context_text import MARKER, encoded_content_bytes, excerpt

    text = '原始中文🙂 ...'
    def fits(value):
        return estimate_tokens(value) <= budget and (max_bytes is None or encoded_content_bytes(value) <= max_bytes)
    if fits(text):
        expected = text
    elif not fits(MARKER):
        expected = ''
    else:
        expected = max((text[:n] + MARKER for n in range(len(text) + 1) if fits(text[:n] + MARKER)), key=len)
    result = excerpt(text, budget, max_bytes=max_bytes, already_truncated=False)
    assert result == expected
    assert result.encode('utf-8').decode('utf-8') == result


def test_excerpt_explicit_truncation_state_preserves_or_reclips_system_suffix():
    from memory.group_context_text import excerpt

    result = excerpt('中文🙂' * 1000, 512, already_truncated=False)
    assert result.endswith(' ...')
    assert excerpt(result, 512, already_truncated=True) == result
    smaller = excerpt(result, 100, max_bytes=150, already_truncated=True)
    assert smaller.endswith(' ...') and not smaller.endswith(' ... ...')
    assert estimate_tokens(smaller) <= 100
    from memory.group_context_text import encoded_content_bytes
    assert encoded_content_bytes(smaller) <= 150


async def test_duplicate_reenters_after_seen_retention_horizon_without_sleep(group_store):
    store, client, key = group_store
    original = await store.append(key, event_id='old', sender_id='u', content='same')
    keys = store.keys(key)
    assert await client.hget(keys[2], 'old') is not None
    # Age just this member past the real Lua TIME-based retention cutoff.
    # This exercises expiry cleanup while the list/sequence remain alive.
    server_time = await client.time()
    now = server_time[0] + server_time[1] / 1_000_000
    await client.zadd(keys[1], {'old': now - store._ttl - 1})
    fresh = await store.append(key, event_id='old', sender_id='u', content='same')
    assert fresh.status == 'appended' and fresh.sequence == original.sequence + 1
    assert (await client.hget(keys[2], 'old')).decode().endswith('|2')
    assert await client.zcard(keys[1]) == await client.hlen(keys[2]) == 1
    assert [m['sequence'] for m in (await store.get_candidates(key)).messages] == [1, 2]


async def test_duplicate_reenters_after_seen_keys_expire_without_sleep(group_store):
    store, client, key = group_store
    first = await store.append(key, event_id='e', sender_id='u', content='same')
    keys = store.keys(key)
    # Redis expires the seen keys synchronously for a deadline in the past.
    for k in keys[1:3]:
        await client.pexpireat(k, 1)
        assert not await client.exists(k)
    fresh = await store.append(key, event_id='e', sender_id='u', content='same')
    assert fresh.status == 'appended' and fresh.sequence == first.sequence + 1
    assert len((await store.get_candidates(key)).messages) == 2


async def test_duplicate_reenters_after_seen_cardinality_eviction(group_store):
    _, client, key = group_store
    store = GroupContextStore(client, window_size=2, seen_limit=3)
    first = await store.append(key, event_id='first', sender_id='u', content='same')
    for i in range(3):
        await store.append(key, event_id=str(i), sender_id='u', content='new')
    keys = store.keys(key)
    assert await client.zscore(keys[1], 'first') is None
    assert await client.hget(keys[2], 'first') is None
    assert await client.zcard(keys[1]) == await client.hlen(keys[2]) == 3
    fresh = await store.append(key, event_id='first', sender_id='u', content='same')
    assert fresh.status == 'appended' and fresh.sequence == first.sequence + 4
    messages = (await store.get_candidates(key)).messages
    assert [m['sequence'] for m in messages] == [4, 5]
    assert [m['event_id'] for m in messages].count('first') == 1
    assert await client.zcard(keys[1]) == await client.hlen(keys[2]) == 3


async def test_store_and_selector_forward_explicit_truncation_provenance(group_store, monkeypatch):
    import application.group_context_selection as selection
    import memory.group_context as storage
    from memory.group_context_text import excerpt

    store, _, key = group_store
    states = []
    def track(text, budget, **kwargs):
        states.append(kwargs['already_truncated'])
        return excerpt(text, budget, **kwargs)
    monkeypatch.setattr(storage, 'excerpt', track)
    monkeypatch.setattr(selection, 'excerpt', track)
    original = '中文🙂' * 1000 + ' ...'
    await store.append(key, event_id='long', sender_id='u', content=original)
    messages = (await store.get_candidates(key)).messages
    assert messages[0]['truncated'] and messages[0]['content'].endswith(' ...')
    snapshot = select_context(messages, trigger_event_id='trigger', sender_id='u',
                              now=datetime.now(UTC), policy=SelectionPolicy(message_tokens=100))
    assert states == [False, True]
    assert snapshot['selected'][0]['truncated']
    clipped = snapshot['selected'][0]['content']
    assert original.startswith(clipped[:-4]) and clipped.endswith(' ...')
    assert estimate_tokens(clipped) <= 100
