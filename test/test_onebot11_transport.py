"""Real loopback WebSocket fixtures; no QQ, provider or business service access."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from channels.onebot11 import OneBot11Channel
from common.onebot import OneBot11Config
from common.types import ChannelType, UnifiedMessage


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


@pytest.fixture
async def channel(tmp_path):
    token = tmp_path / 'token'
    token.write_text('synthetic-secret')
    c = OneBot11Channel(OneBot11Config(
        host='127.0.0.1', port=0, expected_self_id='999', access_token_file=str(token),
        ack_timeout_seconds=.2, status_interval_seconds=10, status_stale_seconds=30,
        send_interval_seconds=0, ingress_queue_size=2))
    await c.start_monitor()
    try:
        yield c
    finally:
        await c.stop_monitor()


def url(channel):
    return f'ws://127.0.0.1:{channel._server.sockets[0].getsockname()[1]}/onebot/v11'


def headers():
    return {'Authorization': 'Bearer synthetic-secret', 'X-Self-ID': '999', 'X-Client-Role': 'Universal'}


@asynccontextmanager
async def bot(channel, *, version='2.2.0-node', login='999', sends=None, mode='ok'):
    async with connect(url(channel), additional_headers=headers()) as ws:
        async def loop():
            async for raw in ws:
                request = json.loads(raw)
                action = request['action']
                data = {'get_login_info': {'user_id': login},
                        'get_version_info': {'app_name': 'SnowLuma', 'app_version': version, 'protocol_version': 'v11'},
                        'get_status': {'online': True, 'good': True}}.get(action)
                response = {'status': 'ok', 'retcode': 0, 'data': data, 'echo': request['echo']}
                if action.startswith('send_'):
                    if sends is not None:
                        sends.append(request)
                    if mode == 'drop':
                        continue
                    response['data'] = {'message_id': -321}
                    if mode != 'ok':
                        response.update(status='failed', retcode=int(mode), data=None)
                await ws.send(json.dumps(response))
        task = asyncio.create_task(loop())
        try:
            yield ws
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def message(**metadata):
    return UnifiedMessage(sender_id='123', content='[CQ:at,qq=all] hi', channel_type=ChannelType.ONEBOT11,
                          metadata={'detail_type': 'private', 'self_id': '999', **metadata})


def event(**extra):
    return {'post_type': 'message', 'self_id': 999, 'user_id': 123, 'message_id': -5,
            'message_type': 'private', 'message': [{'type': 'text', 'data': {'text': 'hello'}}], **extra}


@pytest.mark.parametrize('name,value', [('Authorization', ''), ('Authorization', 'Bearer wrong'),
                                       ('X-Self-ID', 'other'), ('X-Client-Role', 'Event')])
async def test_handshake_rejects_wrong_identity_token_role(channel, name, value):
    h = headers()
    h[name] = value
    with pytest.raises(InvalidStatus):
        async with connect(url(channel), additional_headers=h):
            pass
    assert channel._connection is None


async def test_wrong_path(channel):
    with pytest.raises(InvalidStatus):
        async with connect(url(channel) + '/bad', additional_headers=headers()):
            pass


@pytest.mark.parametrize('version,login', [('2.1.0-node', '999'), ('2.2.0-node', '888')])
async def test_probes_freeze_identity_and_version(channel, version, login):
    async with bot(channel, version=version, login=login):
        await eventually(lambda: channel.counters['connection_failure'] > 0)
        assert not channel.ready


async def test_bidirectional_control_ack_does_not_deadlock(channel):
    received, sent = [], []
    async def callback(msg):
        received.append(await channel.send_with_receipt(message()))
    channel._callback = callback
    async with bot(channel, sends=sent) as ws:
        await eventually(lambda: channel.ready)
        await ws.send(json.dumps(event()))
        await eventually(lambda: received)
        assert received[0].accepted and received[0].provider_message_id == '-321'
        assert sent[0]['params']['message'] == [{'type': 'text', 'data': {'text': '[CQ:at,qq=all] hi'}}]
        assert not await channel.send_message(message(self_id='other'))
        await ws.send('invalid-json')
        await ws.send('[]')
        await ws.send(json.dumps(event(message_id=-6)))
        await eventually(lambda: len(received) == 2)
        assert channel.counters['invalid_frame'] == 2


@pytest.mark.parametrize('mode,state', [('ok', 'accepted'), ('1400', 'failed'), ('1404', 'failed'),
                                       ('1500', 'uncertain'), ('drop', 'uncertain')])
async def test_send_classification_and_negative_message_id(channel, mode, state):
    async with bot(channel, mode=mode):
        await eventually(lambda: channel.ready)
        result = await channel.send_with_receipt(message(delivery_id='d', lease_token='lease', msg_seq=1))
        assert result.state == state
        if result.accepted:
            assert result.provider_message_id == '-321'
            assert 'd:1:lease:' in result.receipt['request_id']


async def test_late_and_duplicate_ack_cannot_complete_new_lease(channel):
    sent = []
    async with bot(channel, mode='drop', sends=sent) as ws:
        await eventually(lambda: channel.ready)
        first = await channel.send_with_receipt(message(delivery_id='d', lease_token='old', msg_seq=1))
        assert first.state == 'uncertain'
        task = asyncio.create_task(channel.send_with_receipt(message(delivery_id='d', lease_token='new', msg_seq=1)))
        await eventually(lambda: len(sent) == 2)
        old = {'status': 'ok', 'retcode': 0, 'data': {'message_id': 9}, 'echo': sent[0]['echo']}
        await ws.send(json.dumps(old))
        await asyncio.sleep(.02)
        assert not task.done()
        await ws.send(json.dumps({**old, 'echo': sent[1]['echo']}))
        assert (await task).accepted
        await ws.send(json.dumps(old))
        await eventually(lambda: channel.counters['late_or_unknown_ack'] == 2)


async def test_new_verified_connection_replaces_old_without_old_disconnect_clearing_it(channel):
    async with bot(channel):
        await eventually(lambda: channel.ready)
        first = channel._connection.epoch
        async with bot(channel):
            await eventually(lambda: channel.ready and channel._connection.epoch > first)
            await eventually(lambda: len(channel._connections) == 1)
            assert (await channel.send_with_receipt(message())).accepted


async def test_changed_event_identity_disconnects(channel):
    async with bot(channel) as ws:
        await eventually(lambda: channel.ready)
        await ws.send(json.dumps(event(self_id=888)))
        await eventually(lambda: not channel.ready)
        assert channel.counters['identity_rejected'] == 1


async def test_bounded_ingress_overflow_disconnects_without_blocking_acks(channel):
    gate = asyncio.Event()
    async def slow(msg):
        await gate.wait()
    channel._callback = slow
    async with bot(channel) as ws:
        await eventually(lambda: channel.ready)
        for i in range(8):
            await ws.send(json.dumps(event(message_id=i)))
        await eventually(lambda: channel.counters['ingress_overflow'] == 1)
        assert channel._queue.qsize() <= 2
        assert not channel.ready
    gate.set()


@pytest.mark.parametrize('status_event', [
    {'post_type': 'meta_event', 'status': {'online': False}},
    {'post_type': 'notice', 'notice_type': 'bot_status', 'sub_type': 'offline'},
    {'post_type': 'notice', 'notice_type': 'bot_offline'},
])
async def test_status_event_invalidation_and_no_connection_send(channel, status_event):
    assert (await channel.send_with_receipt(message())).state == 'failed'
    async with bot(channel) as ws:
        await eventually(lambda: channel.ready)
        await ws.send(json.dumps({'self_id': 999, **status_event}))
        await eventually(lambda: not channel.ready)
        assert channel.diagnostics()['listening']
        assert (await channel.send_with_receipt(message())).state == 'failed'


@pytest.mark.parametrize('at,expected', [('999', True), ('all', False), ('888', False)])
async def test_normalize_mentions_and_top_level_identity(at, expected):
    channel = OneBot11Channel()
    channel.bot_name = 'TestBot'
    msg = await channel.normalize_message(event(message_type='group', group_id=42, message=[
        {'type': 'at', 'data': {'qq': at}}, {'type': 'text', 'data': {'text': '@TestBot hello'}}]))
    assert msg.metadata['is_at_bot'] == expected
    assert msg.message_id == '-5' and msg.sender_id == '123'


async def test_images_not_local_paths_and_nonmessage_events():
    channel = OneBot11Channel()
    msg = await channel.normalize_message(event(message=[
        {'type': 'image', 'data': {'file': '/etc/passwd'}},
        {'type': 'image', 'data': {'url': 'https://example.invalid/image'}},
        {'type': 'record', 'data': {}}]))
    assert msg.metadata['image_urls'] == ['https://example.invalid/image']
    assert msg.metadata['unsupported_segments'] == ['record']
    for post_type in ['notice', 'request', 'message_sent', 'meta_event']:
        assert await channel.normalize_message(event(post_type=post_type)) is None
    with pytest.raises(ValueError):
        await channel.normalize_message(event(sender={'user_id': 444}))
    with pytest.raises(ValueError):
        await channel.normalize_message(event(message_id=None))


async def test_temporary_private_route_and_cq_string():
    from application.conversation import normalize_qq_message
    channel = OneBot11Channel()
    raw = event(sub_type='group', group_id=42, message='[CQ:at,qq=999] hello &#91;CQ:at,qq=all&#93;')
    msg = await channel.normalize_message(raw)
    source = normalize_qq_message(msg, 'onebot11')
    assert source.route['room_id'] == '123/temporary/42'
    assert source.origin['metadata']['group_id'] == 42
    assert msg.content == 'hello [CQ:at,qq=all]'
    with pytest.raises(ValueError):
        await channel.normalize_message({k: v for k, v in raw.items() if k != 'group_id'})


async def test_disconnect_inflight_and_reconnect_do_not_reuse_echo(channel):
    sent = []
    async with bot(channel, sends=sent, mode='drop') as ws:
        await eventually(lambda: channel.ready)
        task = asyncio.create_task(channel.send_with_receipt(message(delivery_id='d', lease_token='first', msg_seq=1)))
        await eventually(lambda: sent)
        old_echo = sent[0]['echo']
        await ws.close()
        assert (await task).state == 'uncertain'
    async with bot(channel, sends=sent, mode='drop') as ws:
        await eventually(lambda: channel.ready)
        task = asyncio.create_task(channel.send_with_receipt(message(delivery_id='d', lease_token='next', msg_seq=1)))
        await eventually(lambda: len(sent) == 2)
        await ws.send(json.dumps({'status': 'ok', 'retcode': 0, 'data': {'message_id': 1}, 'echo': old_echo}))
        await asyncio.sleep(.02)
        assert not task.done()
        await ws.send(json.dumps({'status': 'ok', 'retcode': 0, 'data': {'message_id': 2}, 'echo': sent[-1]['echo']}))
        assert (await task).provider_message_id == '2'


async def test_true_rate_spacing_and_stale_readiness(channel):
    channel.config.send_interval_seconds = .03
    sent = []
    async with bot(channel, sends=sent):
        await eventually(lambda: channel.ready)
        loop = asyncio.get_running_loop()
        before = loop.time()
        results = await asyncio.gather(*(channel.send_with_receipt(message()) for _ in range(3)))
        assert all(result.accepted for result in results)
        assert loop.time() - before >= .05
        channel._connection.status_at -= 100
        assert not channel.ready
        assert (await channel.send_with_receipt(message())).state == 'failed'


async def test_bind_failure_is_awaited_not_background_success(channel, tmp_path):
    config = OneBot11Config(host='127.0.0.1', port=channel._server.sockets[0].getsockname()[1],
                           access_token_file=channel.config.access_token_file, expected_self_id='999')
    second = OneBot11Channel(config)
    with pytest.raises(OSError):
        await second.start_monitor()
    assert not second.is_monitoring


@pytest.mark.parametrize('overrides', [{'ack_timeout_seconds': 0}, {'ingress_queue_size': 0},
                                       {'send_interval_seconds': -1}, {'path': 'bad'},
                                       {'status_stale_seconds': 10}, {'port': 99999}])
def test_invalid_configuration_fails_before_listener(overrides):
    with pytest.raises(ValueError):
        OneBot11Config(**overrides)


async def test_missing_secret_refuses_listener(tmp_path):
    channel = OneBot11Channel(OneBot11Config(access_token_file=str(tmp_path / 'missing')))
    with pytest.raises(FileNotFoundError):
        await channel.start_monitor()
    assert not channel.is_monitoring


def test_worker_can_disable_optional_qq_with_environment():
    from orchestration.config import AppConfig
    config = AppConfig._from_dict({'channels': {'enabled': ['onebot11'],
                                              'onebot11': {'port': 18082}}})
    config._apply_env_overrides({'HPAGENT_CHANNELS': ''})
    assert config.channels.enabled == []
    assert config.channels.onebot11.port == 18082
    config._apply_env_overrides({'HPAGENT_CHANNELS': 'onebot11', 'ONEBOT_EXPECTED_SELF_ID': '999999'})
    assert config.channels.enabled == ['onebot11']
    assert config.channels.onebot11.expected_self_id == '999999'
