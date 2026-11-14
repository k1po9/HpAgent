"""Dedicated PostgreSQL + Redis contracts for frozen shared group background."""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from support.qq_messages import qq_message

from application.chat.service import ChatCommandService
from application.context_assembly import ContextAssemblyService
from application.context_builder import HarnessContextBuilder
from application.conversation import normalize_qq_message
from application.ingress import MessageIngressService
from application.qq_delivery import QQDeliveryAdapter
from common.channel_result import ChannelSendResult
from delivery.service import DeliveryService
from memory.group_context import GroupContextStore
from test.web_persistence.test_qq_canonical_ingress import bind, service

pytestmark = pytest.mark.postgres


@pytest.fixture
async def cache():
    from redis.asyncio import Redis
    url = os.getenv('GROUP_CONTEXT_TEST_REDIS_URL')
    if not url:
        pytest.skip('requires explicit isolated GROUP_CONTEXT_TEST_REDIS_URL')
    client = Redis.from_url(url)
    yield GroupContextStore(client)
    await client.aclose()


async def test_shared_background_is_frozen_but_account_history_stays_private(
    db, account_id, worker_database_url, cache,
):
    other = uuid4()
    db.execute('INSERT INTO accounts(account_id) VALUES (%s)', (other,))
    bind(db, account_id)
    bind(db, other, sender='456')
    room = str(uuid4())
    ingress = MessageIngressService(cache, service(worker_database_url))
    await ingress.handle(qq_message('ambient', 'SHARED', sender='outsider', scope='group', room=room, trigger=False))
    first = await ingress.handle(qq_message('a', 'A REQUEST', scope='group', room=room))
    commands = ChatCommandService(worker_database_url)
    commands.start_run(account_id, UUID(first['run_id']))
    commands.complete_run(account_id, UUID(first['run_id']), 'A PUBLIC ANSWER')
    # A private message in another Conversation must never enter shared cache.
    private = await service(worker_database_url).accept(qq_message('private', 'ACCOUNT SECRET'), 'onebot11')
    commands.fail_run(account_id, UUID(private['run_id']), 'fixture')

    class Router:
        async def send_with_receipt(self, message):
            return ChannelSendResult('accepted', provider_message_id='provider-bot')
    await DeliveryService(worker_database_url, QQDeliveryAdapter(Router(), cache)).deliver_once()
    second = await ingress.handle(qq_message('b', 'B REQUEST', sender='456', scope='group', room=room))
    context = ContextAssemblyService(worker_database_url, HarnessContextBuilder())
    b = context.load_base(other, UUID(second['run_id']))
    rendered = str(context.compose(b, ()))
    assert all(word in rendered for word in ['SHARED', 'A PUBLIC ANSWER', 'B REQUEST'])
    assert 'ACCOUNT SECRET' not in rendered
    assert len(b.short_term_events) == 1
    frozen = b.origin['group_context']
    source = normalize_qq_message(qq_message(scope='group', room=room), 'onebot11')
    for key in cache.keys(source.context_key):
        await cache._r.delete(key)
    assert context.compose(context.load_base(other, UUID(second['run_id'])), ()) == context.compose(b, ())
    # Re-delivery after cache loss cannot replace the already committed origin.
    replay = await ingress.handle(qq_message('b', 'B REQUEST', sender='456', scope='group', room=room))
    assert replay.replayed
    assert context.load_base(other, UUID(second['run_id'])).origin['group_context'] == frozen
    commands.fail_run(other, UUID(second['run_id']), 'fixture')
    retry = commands.retry_run(other, UUID(second['run_id']), str(uuid4()))
    assert context.load_base(other, UUID(retry['run_id'])).origin['group_context'] == frozen


async def test_thread_and_bot_isolation_with_real_candidates(db, account_id, worker_database_url, cache):
    bind(db, account_id)
    room = str(uuid4())
    ingress = MessageIngressService(cache, service(worker_database_url))
    for thread, bot in [('other-thread', 'bot'), ('', 'other-bot')]:
        await ingress.handle(qq_message(str(uuid4()), 'ISOLATED', sender='outsider', scope='group',
                                        room=room, thread=thread, bot=bot, trigger=False))
    result = await ingress.handle(qq_message('trigger', 'CURRENT', scope='group', room=room))
    base = ContextAssemblyService(worker_database_url, HarnessContextBuilder()).load_base(account_id, UUID(result['run_id']))
    assert base.origin['group_context']['selected_count'] == 0
