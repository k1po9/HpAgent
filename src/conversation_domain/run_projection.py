"""Chat-specific projection of an owner-neutral Run terminal fact."""

from __future__ import annotations

import json

from uuid6 import uuid7

from persistence.repositories import MessageRepository, OutboxRepository


def project_terminal(uow, run, status, content=None):
    run_id = run['run_id']
    MessageRepository().set_terminal(uow, run_id,
                                     {'succeeded':'completed','failed':'failed','cancelled':'aborted'}[status], content)
    if status=='succeeded':
        uow.execute('INSERT INTO message_files(account_id,conversation_id,message_id,file_id,role,ordinal) '
                    'SELECT rf.account_id,m.conversation_id,m.message_id,rf.file_id,\'output\','
                    'row_number() OVER (ORDER BY rf.created_at,rf.file_id)-1 '
                    'FROM run_files rf JOIN messages m ON m.produced_by_run_id=rf.run_id '
                    'WHERE rf.run_id=%s AND rf.direction=\'output\' ON CONFLICT(message_id,file_id) DO NOTHING',
                    (run_id,))
        from conversation_domain.delivery import enqueue_qq_result

        enqueue_qq_result(uow, run_id)
        OutboxRepository().enqueue(uow, uuid7(), run['account_id'], 'retain_memory',
                                   f'retain-memory:{run_id}', run['conversation_id'], run_id,
                                   json.dumps({'run_id':str(run_id),'version':1}))
    event_id = uuid7()
    OutboxRepository().enqueue(uow, event_id, run['account_id'], 'publish_terminal_event',
                               f'terminal:{run_id}:{status}', run['conversation_id'], run_id,
                               json.dumps({'run_id':str(run_id),'version':1,'terminal_status':status,
                                           'terminal_event_id':str(event_id)}))
