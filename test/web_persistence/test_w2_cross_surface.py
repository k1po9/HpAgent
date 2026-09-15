"""G06: actual QQ adapter and Web commands under one account/PG authority."""
from uuid import UUID, uuid4

import pytest
from support.qq_messages import qq_message

from application.context_assembly import ContextAssemblyService
from conversation_domain.commands import CommandService
from conversation_domain.sessions import ConversationSessionService
from harness.context_builder import HarnessContextBuilder
from web_persistence.test_qq_canonical_ingress import bind, service
from web_domain.errors import ConversationBusy

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_same_account_web_private_group_context_rotation_cancel_retry(
    db, account_id, database_url, worker_database_url,
):
    bind(db, account_id)
    web = CommandService(database_url)
    worker = CommandService(worker_database_url)
    qq = service(worker_database_url)
    cid = UUID(web.create_conversation(account_id, str(uuid4()))['conversation_id'])
    w = web.send_message(account_id, cid, str(uuid4()), 'web-only-secret')
    private_message = qq_message('private', 'private-only-secret')
    p = await qq.accept(private_message, 'napcat')
    g = await qq.accept(qq_message('group', 'group-only-text', scope='group'), 'napcat')
    assert len({str(cid), p['conversation_id'], g['conversation_id']}) == 3
    assert len({w['session_id'], p['session_id'], g['session_id']}) == 3
    assert (await qq.accept(private_message, 'napcat'))['run_id'] == p['run_id']
    context = ContextAssemblyService(worker_database_url, HarnessContextBuilder())
    for result, own, excluded in (
        (w, 'web-only-secret', ('private-only-secret', 'group-only-text')),
        (p, 'private-only-secret', ('web-only-secret', 'group-only-text')),
        (g, 'group-only-text', ('web-only-secret', 'private-only-secret')),
    ):
        assembled = str(context.compose(context.load_base(account_id, UUID(result['run_id'])), ()))
        assert own in assembled
        assert all(text not in assembled for text in excluded)
    pcid = UUID(p['conversation_id'])
    with pytest.raises(ConversationBusy):
        web.send_message(account_id, pcid, str(uuid4()), 'explicit Web view of QQ conversation')
    cancelled = await qq.accept(qq_message('cancel', '/cancel'), 'napcat')
    assert cancelled['run_id'] == p['run_id']
    session = ConversationSessionService(database_url).rotate_active(account_id, pcid)
    failed = await qq.accept(qq_message('failure', 'retryable QQ request'), 'napcat')
    assert failed['session_id'] == str(session)
    worker.fail_run(account_id, UUID(failed['run_id']), 'test_failure')
    retried = web.retry_run(account_id, UUID(failed['run_id']), str(uuid4()))
    assert retried['run']['session_id'] == str(session)
    retry_id = UUID(retried['run']['run_id'])
    worker.start_run(account_id, retry_id)
    worker.complete_run(account_id, retry_id, 'retried committed answer')
    assert db.execute('SELECT count(*) FROM qq_deliveries WHERE run_id=%s', (retry_id,)).fetchone()[0] == 1
    assert db.execute('SELECT status FROM runs WHERE run_id=%s', (UUID(g['run_id']),)).fetchone()[0] == 'queued'
