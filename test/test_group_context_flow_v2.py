from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from support.qq_messages import qq_message

from application.conversation import normalize_qq_message
from application.ingress import MessageIngressService
from application.qq_delivery import QQDeliveryAdapter
from common.channel_result import ChannelSendResult
from memory.group_context import AppendResult, CandidateResult


async def test_ingress_freezes_only_selection_and_canonical_identity():
    message = qq_message('trigger', scope='group')
    source = normalize_qq_message(message, 'onebot11')
    now = datetime.now(UTC).isoformat()
    cache = Mock(append=AsyncMock(return_value=AppendResult('appended', 3)),
                 get_candidates=AsyncMock(return_value=CandidateResult('ok', (
                     dict(event_id='ambient', sender_id='other', sender_name='other', sequence=1,
                          content='shared fact', event_time=now, thread_id='', message_type='user_message'),
                     dict(event_id=source.event_id, sender_id='123', sequence=3,
                          content='hello', event_time=now, thread_id='', message_type='user_message'),
                 ))))
    commands = Mock(accept=AsyncMock(return_value=None))
    await MessageIngressService(cache, commands).handle(message)
    assert cache.append.call_args.args == (source.context_key,)
    assert cache.append.call_args.kwargs['event_id'] == source.event_id
    frozen = commands.accept.call_args.kwargs['group_context']
    assert frozen['selected_count'] == 1
    assert frozen['selected'][0]['event_id'] == 'ambient'
    assert frozen['high_watermark'] == 3


async def test_cache_timeout_and_conflict_do_not_block_admission():
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    for append in [slow, AsyncMock(return_value=AppendResult('conflict'))]:
        commands = Mock(accept=AsyncMock(return_value=None))
        cache = Mock(append=append, get_candidates=AsyncMock())
        ingress = MessageIngressService(cache, commands, context_timeout_seconds=.01)
        await asyncio.wait_for(ingress.handle(qq_message(scope='group')), .2)
        assert commands.accept.call_args.kwargs['group_context']['status'] == 'degraded'
        cache.get_candidates.assert_not_awaited()


def delivery_row(channel='onebot11', scope='group'):
    message = qq_message('trigger', scope=scope)
    origin = normalize_qq_message(message, 'onebot11').origin
    if channel == 'official_qq':
        origin = {**origin, 'channel_type': channel, 'bot_id': 'app',
                  'metadata': {'detail_type': scope, 'group_openid': '42'}}
    return dict(route=origin, payload={'content': 'x' * 1600}, content_scope='summary',
                account_id='account', run_id='run', notification_id='notification',
                delivery_id='delivery', lease_token='lease1', source_message_id='assistant-pg-id',
                purpose='result', work_id=None)


@pytest.mark.parametrize('state', ['accepted', 'failed', 'uncertain'])
async def test_delivery_only_accepted_parts_return_to_cache(state):
    router = Mock(send_with_receipt=AsyncMock(return_value=ChannelSendResult(state, provider_message_id='99')))
    cache = Mock(append=AsyncMock(return_value=AppendResult('appended', 1)))
    adapter = QQDeliveryAdapter(router, cache)
    row = delivery_row()
    result = await adapter.send(row, 1)
    assert result.state == state
    if state != 'accepted':
        cache.append.assert_not_awaited()
        return
    args = cache.append.call_args
    assert args.kwargs['content'] == 'x' * 100
    assert args.kwargs['source_message_id'] == 'assistant-pg-id'
    assert args.kwargs['message_type'] == 'assistant_final'
    assert args.kwargs['part_index'] == 1
    first_id = args.kwargs['event_id']
    row['lease_token'] = 'lease2'
    await adapter.send(row, 1)
    assert cache.append.call_args.kwargs['event_id'] == first_id
    echo = qq_message('99', 'x' * 100, sender='bot', scope='group')
    assert normalize_qq_message(echo, 'onebot11').event_id == first_id


async def test_cache_failure_after_send_preserves_receipt_and_official_fallback_id():
    receipt = ChannelSendResult('accepted')
    router = Mock(send_with_receipt=AsyncMock(return_value=receipt))
    cache = Mock(append=AsyncMock(side_effect=ConnectionError('down')))
    adapter = QQDeliveryAdapter(router, cache)
    row = delivery_row('official_qq')
    assert await adapter.send(row, 0) is receipt
    assert cache.append.call_args.kwargs['event_id'] == 'delivery:delivery:0'
    assert cache.append.call_args.kwargs['sender_id'] == 'app'


async def test_own_bot_echo_never_promotes_control_or_uncertain_to_background():
    cache = Mock(append=AsyncMock())
    commands = Mock(accept=AsyncMock())
    for enabled_cache in (cache, None):
        await MessageIngressService(enabled_cache, commands).handle(
            qq_message('echo', 'control or uncertain text', sender='bot', scope='group'))
    cache.append.assert_not_awaited()
    commands.accept.assert_not_awaited()


async def test_cancellation_during_cache_append_preserves_known_acceptance():
    entered = asyncio.Event()
    async def slow(*args, **kwargs):
        entered.set()
        await asyncio.sleep(10)
    receipt = ChannelSendResult('accepted', provider_message_id='99')
    router = Mock(send_with_receipt=AsyncMock(return_value=receipt))
    adapter = QQDeliveryAdapter(router, Mock(append=slow))
    task = asyncio.create_task(adapter.send(delivery_row(), 0))
    await asyncio.wait_for(entered.wait(), .1)
    task.cancel()  # DeliveryService wait_for deadline lands after provider ack.
    assert await task is receipt


@pytest.mark.parametrize('event,scope,room', [
    ('GROUP_AT_MESSAGE_CREATE', 'group', {'group_openid': 'group'}),
    ('AT_MESSAGE_CREATE', 'guild', {'guild_id': 'guild', 'channel_id': 'channel'}),
])
async def test_official_ingress_and_accepted_delivery_share_context_key(event, scope, room):
    from application.conversation import context_key_for_route
    from channels.official_qq import OfficialQQChannel

    channel = OfficialQQChannel()
    channel._app_id = 'app'
    message = await channel.normalize_message({'t': event, 'd': {
        'id': 'provider-trigger', 'content': 'current', 'author': {'id': 'user'}, **room}})
    source = normalize_qq_message(message, 'official_qq')
    cache = Mock(append=AsyncMock(return_value=AppendResult('appended', 1)),
                 get_candidates=AsyncMock(return_value=CandidateResult('empty')))
    commands = Mock(accept=AsyncMock(return_value=None))
    await MessageIngressService(cache, commands).handle(message)
    ingress_key = cache.append.call_args.args[0]
    row = {**delivery_row(), 'route': {**source.origin, 'context_key': source.context_key}}
    receipt = ChannelSendResult('accepted', provider_message_id='provider-reply')
    await QQDeliveryAdapter(Mock(send_with_receipt=AsyncMock(return_value=receipt)), cache).send(row, 0)
    assert cache.append.call_args.args[0] == ingress_key == source.context_key
    assert context_key_for_route({**source.route, 'thread_id': 'other'}) != ingress_key
    assert source.route['scope'] == scope


async def test_unknown_duplicate_sequence_degrades_instead_of_using_current_window():
    cache = Mock(append=AsyncMock(return_value=AppendResult('duplicate', 0)), get_candidates=AsyncMock())
    commands = Mock(accept=AsyncMock(return_value=None))
    await MessageIngressService(cache, commands).handle(qq_message(scope='group'))
    assert commands.accept.call_args.kwargs['group_context']['status'] == 'degraded'
    assert commands.accept.call_args.kwargs['group_context']['degraded_reason'] == 'sequence_unavailable'
    cache.get_candidates.assert_not_awaited()


@pytest.mark.parametrize('kind', ['work', 'error'])
async def test_notifications_still_send_but_never_call_context_append(kind):
    cache = Mock(append=AsyncMock())
    row = delivery_row()
    row['work_id'] = 'work' if kind == 'work' else None
    if kind == 'error':
        row['source_message_id'] = None
    receipt = ChannelSendResult('accepted', provider_message_id='notice')
    router = Mock(send_with_receipt=AsyncMock(return_value=receipt))
    assert await QQDeliveryAdapter(router, cache).send(row, 0) is receipt
    router.send_with_receipt.assert_awaited_once()
    cache.append.assert_not_awaited()
