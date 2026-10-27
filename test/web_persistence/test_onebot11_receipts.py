"""Canonical PG commands + real WS peer, including uncertainty and lease loss."""
import asyncio
from uuid import UUID

import pytest
from support.qq_messages import qq_message
from test_onebot11_transport import bot, eventually
from test_onebot11_transport import channel as channel

from application.qq_delivery import QQDeliveryAdapter
from channels.router import ChannelRouter
from common.types import ChannelType
from conversation_domain.commands import CommandService
from delivery.service import DeliveryService
from web_persistence.test_qq_canonical_ingress import bind, service

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def prepared(db, account_id, database, channel):
    bind(db, account_id)
    accepted = await service(database).accept(qq_message(bot='999'), 'onebot11')
    commands = CommandService(database)
    run_id = UUID(accepted['run_id'])
    commands.start_run(account_id, run_id)
    commands.complete_run(account_id, run_id, '中文😀[CQ:at,qq=all]' + 'x' * 1600)
    router = ChannelRouter()
    router.register(ChannelType.ONEBOT11, channel)
    return DeliveryService(database, QQDeliveryAdapter(router))


def receipt(db):
    return db.execute('SELECT state,next_part,provider_receipt FROM deliveries').fetchone()


async def test_real_ws_parts_survive_uncertain_and_explicit_retry(db, account_id, worker_database_url, channel):
    sender = await prepared(db, account_id, worker_database_url, channel)
    sent = []
    async with bot(channel, sends=sent):
        await eventually(lambda: channel.ready)
        await sender.deliver_once()
    state, part, proof = receipt(db)
    assert (state, part) == ('pending', 1)
    assert proof['parts'][0]['provider_message_id'] == '-321'
    assert proof['level'] == 'provider_accepted'
    assert sent[0]['params']['message'][-1]['data']['text'].startswith('中文😀[CQ:at,qq=all]')
    db.execute('UPDATE deliveries SET available_at=now()')
    async with bot(channel, sends=sent, mode='drop'):
        await eventually(lambda: channel.ready)
        await sender.deliver_once()
    state, part, proof = receipt(db)
    assert (state, part) == ('uncertain', 1)
    assert len(proof['parts']) == 1
    assert not await sender.deliver_once()
    delivery_id = db.execute('SELECT delivery_id FROM deliveries').fetchone()[0]
    assert sender.retry_uncertain(account_id, delivery_id)
    async with bot(channel, sends=sent):
        await eventually(lambda: channel.ready)
        await sender.deliver_once()
    state, part, proof = receipt(db)
    assert (state, part) == ('accepted', 2)
    assert [p['part'] for p in proof['parts']] == [1, 2]
    assert all(p['provider_message_id'] == '-321' for p in proof['parts'])
    assert sent[0]['echo'] != sent[1]['echo'] != sent[2]['echo']
    assert sent[1]['params'] == sent[2]['params']
    assert len(sent[2]['params']['message']) == 1
    assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 1


async def test_pg_finish_failure_preserves_uncertain_after_sending_lease(db, account_id, worker_database_url, channel, monkeypatch):
    sender = await prepared(db, account_id, worker_database_url, channel)
    original = sender.finish
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic finish failure')
    monkeypatch.setattr(sender, 'finish', fail)
    async with bot(channel):
        await eventually(lambda: channel.ready)
        with pytest.raises(RuntimeError):
            await sender.deliver_once()
    assert receipt(db)[:2] == ('sending', 0)
    old = db.execute('SELECT delivery_id,lease_token FROM deliveries').fetchone()
    db.execute("UPDATE deliveries SET lease_until=now()-interval '1 second'")
    monkeypatch.setattr(sender, 'finish', original)
    assert sender.claim() is None
    assert receipt(db)[:2] == ('uncertain', 0)
    assert sender.retry_uncertain(account_id, old[0])
    row = sender.claim()
    assert str(row['lease_token']) != str(old[1])
    sender.finish({**row, 'lease_token': old[1]}, 'accepted', 1, {'level': 'provider_accepted'})
    assert receipt(db)[:2] == ('sending', 0)
    db.execute("UPDATE deliveries SET lease_until=now()-interval '1 second'")
    assert sender.claim() is None
    assert receipt(db)[:2] == ('uncertain', 0)
    await asyncio.sleep(0)


async def test_sigkill_after_real_ack_recovers_to_uncertain_without_resending(
    db, account_id, worker_database_url, channel, tmp_path,
):
    import json
    import os
    import sys
    from pathlib import Path

    from websockets.asyncio.client import connect

    sender = await prepared(db, account_id, worker_database_url, channel)
    token = tmp_path / 'synthetic-onebot-token'
    token.write_text('synthetic-secret')
    harness = Path(__file__).resolve().parents[1] / 'fixtures/onebot11_delivery_sender.py'
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(harness), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, 'WORKER_DATABASE_URL': worker_database_url,
             'ONEBOT_TEST_TOKEN_FILE': str(token)})
    sent = []
    try:
        address = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
        async with connect(f'ws://127.0.0.1:{address["port"]}/onebot/v11', additional_headers={
            'Authorization': 'Bearer synthetic-secret', 'X-Self-ID': '999', 'X-Client-Role': 'Universal',
        }) as ws:
            async def peer():
                async for raw in ws:
                    request = json.loads(raw)
                    action = request['action']
                    data = {'get_login_info': {'user_id': '999'},
                            'get_version_info': {'app_name': 'SnowLuma', 'app_version': '2.2.0-node', 'protocol_version': 'v11'},
                            'get_status': {'online': True, 'good': True}}.get(action)
                    if action.startswith('send_'):
                        sent.append(request)
                        data = {'message_id': -712}
                    await ws.send(json.dumps({'status': 'ok', 'retcode': 0, 'data': data, 'echo': request['echo']}))
            peer_task = asyncio.create_task(peer())
            try:
                marker = await asyncio.wait_for(process.stdout.readline(), 15)
                assert marker.strip() == b'PROVIDER_ACK_BEFORE_PG_FINISH'
                assert len(sent) == 1
                assert receipt(db)[:2] == ('sending', 0)
                process.kill()
                await asyncio.wait_for(process.wait(), 5)
            finally:
                peer_task.cancel()
                await asyncio.gather(peer_task, return_exceptions=True)
        db.execute("UPDATE deliveries SET lease_until=now()-interval '1 second'")
        assert sender.claim() is None
        assert receipt(db)[:2] == ('uncertain', 0)
        assert not await sender.deliver_once()
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 1
        assert len(sent) == 1
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()


async def test_new_channel_constraint_refuses_legacy_alias(db, account_id):
    from uuid import uuid4

    from psycopg.errors import CheckViolation

    with pytest.raises(CheckViolation) as failure:
        db.execute(
            "INSERT INTO delivery_targets(target_id,account_id,channel,identity_provider,identity_subject,"
            "audience,content_scope,target_version) VALUES (%s,%s,'napcat','qq','napcat:123','private','content',1)",
            (uuid4(), account_id))
    assert failure.value.diag.constraint_name == 'delivery_targets_channel_check'
