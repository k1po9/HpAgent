"""S5 source identity, ordinary chat isolation and capability query ownership."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from application.main_agent import create_main_work_tools
from conversation_domain.commands import CommandService
from web_domain.errors import ResourceNotFound
from work_domain.models import Requirement

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_main_source_replay_chat_independence_and_revision(commands, account_id, urls, owner):
    chat = CommandService(urls[1])
    cid = UUID(chat.create_conversation(account_id, str(uuid4())).body['conversation_id'])
    sent = chat.send_message(account_id, cid, str(uuid4()), 'Continue in background')
    source = owner.execute('SELECT trigger_message_id FROM runs WHERE run_id=%s',
                           (sent['run_id'],)).fetchone()['trigger_message_id']
    context = {'account_id': str(account_id), 'conversation_id': str(cid), 'trigger_message_id': str(source)}
    tools = {t.name: t for t in create_main_work_tools(context, commands)}
    requirement = Requirement('Research then make HTML', 'generic_work', {'schema_version': 1})
    payload = {'title': 'Background', 'requirement': requirement.to_dict(), 'mandate_slot': 'goal'}
    work = json.loads(await tools['accept_work'].ainvoke(payload))['work']
    assert json.loads(await tools['accept_work'].ainvoke(payload))['work']['work_id'] == work['work_id']
    assert work['sources'] == [{'conversation_id': str(cid), 'source_message_id': str(source)}]
    count = owner.execute('SELECT count(*) AS n FROM messages WHERE conversation_id=%s', (cid,)).fetchone()['n']
    run = commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version']).body['run']
    # Work admission neither takes the chat slot nor creates a Message.
    assert owner.execute('SELECT count(*) AS n FROM messages WHERE conversation_id=%s', (cid,)).fetchone()['n'] == count
    active = owner.execute("SELECT run_id FROM runs WHERE account_id=%s AND conversation_id=%s "
                           "AND source_kind='chat' AND status IN ('queued','running','cancelling')",
                           (account_id, cid)).fetchall()
    assert [row['run_id'] for row in active] == [UUID(sent['run_id'])]
    before = commands.get(account_id, UUID(work['work_id']))['work']
    await tools['get_work'].ainvoke({'work_id': work['work_id']})
    assert commands.get(account_id, UUID(work['work_id']))['work']['row_version'] == before['row_version']
    revised = json.loads(await tools['revise_work'].ainvoke({
        'work_id': work['work_id'], 'row_version': before['row_version'],
        'requirement': replace(requirement, objective='Use the new constraints').to_dict(),
        'operation_id': str(uuid4()),
    }))['work']
    assert revised['current_requirement_revision'] == 2
    assert owner.execute('SELECT status FROM runs WHERE run_id=%s', (run['run_id'],)).fetchone()['status'] == 'cancelling'
    assert revised['sources'] == work['sources']


def test_source_without_conversation_infers_exact_link_and_rejects_other_account(commands, account_id, urls, owner):
    chat = CommandService(urls[1])
    cid = UUID(chat.create_conversation(account_id, str(uuid4())).body['conversation_id'])
    sent = chat.send_message(account_id, cid, str(uuid4()), 'Background')
    source = owner.execute('SELECT trigger_message_id FROM runs WHERE run_id=%s', (sent['run_id'],)).fetchone()['trigger_message_id']
    req = Requirement('Goal', 'generic_work', {'schema_version': 1})
    work = commands.accept(account_id, str(uuid4()), 'Goal', req, source_message_id=source).body['work']
    assert work['sources'] == [{'conversation_id': str(cid), 'source_message_id': str(source)}]
    other = uuid4()
    owner.execute('INSERT INTO accounts(account_id) VALUES (%s)', (other,))
    with pytest.raises(ResourceNotFound):
        commands.accept(other, str(uuid4()), 'Goal', req, source_message_id=source)
