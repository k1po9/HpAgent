"""One command boundary shared by API and application tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from uuid6 import uuid7

from persistence.command_result import CommandResult
from persistence.repositories import AccountRepository, IdempotencyRepository
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import DomainError, IdempotencyConflict, ResourceNotFound
from work_domain.models import Requirement, continuation, digest
from work_domain.persistence import WorkRepository, dto


class WorkConflict(DomainError):
    def __init__(self, current: dict, reason: str = 'version_conflict'):
        super().__init__(reason)
        self.current = dto(current)
        self.reason = reason


class WorkCommandService:
    def __init__(self, database: object, *, budget_mode: str = 'enforce'):
        self.database = database
        self.budget_mode = budget_mode
        self.repo = WorkRepository()
        self.idempotency = IdempotencyRepository()

    def _claim(self, uow, account_id, operation, key, payload):
        request_hash = digest(payload)
        row = self.idempotency.claim(uow, uuid7(), account_id, operation, key, request_hash,
                                     datetime.now(UTC) + timedelta(days=7))
        if row is not None:
            if bytes(row['request_hash']) != request_hash:
                raise IdempotencyConflict()
            if row['status'] != 'completed':
                raise WorkConflict({}, 'command_in_progress')
            return CommandResult(int(row['response_status']), dict(row['response_body']), replayed=True), None
        row = uow.execute('SELECT idempotency_command_id FROM idempotency_commands '
                          'WHERE account_id=%s AND operation=%s AND idempotency_key=%s',
                          (account_id, operation, key)).fetchone()
        return None, row['idempotency_command_id']

    def _complete(self, uow, account_id, operation, key, work_id, status=200, **extra):
        body = {'work': self._snapshot(uow, account_id, work_id), **dto(extra)}
        self.idempotency.complete(uow, account_id, operation, key, status, json.dumps(body))
        return CommandResult(status, body)

    def _snapshot(self, uow, account_id, work_id):
        work = self.repo.get(uow, account_id, work_id)
        work['requirement'] = self.repo.requirement(uow, account_id, work_id,
                                                   work['current_requirement_revision'])
        work['conversation_ids'] = [row['conversation_id'] for row in uow.execute(
            'SELECT conversation_id FROM work_conversations WHERE account_id=%s AND work_id=%s '
            'ORDER BY conversation_id', (account_id, work_id)).fetchall()]
        return dto(work)

    def get(self, account_id: UUID, work_id: UUID) -> dict:
        with UnitOfWork(self.database) as uow:
            return {'work': self._snapshot(uow, account_id, work_id)}

    def list(self, account_id: UUID, before: UUID | None = None) -> dict:
        with UnitOfWork(self.database) as uow:
            rows = uow.execute('SELECT * FROM works WHERE account_id=%s AND '
                               '(%s::uuid IS NULL OR work_id<%s) ORDER BY work_id DESC LIMIT 51',
                               (account_id, before, before)).fetchall()
            return {'items': [self._snapshot(uow, account_id, row['work_id']) for row in rows[:50]],
                    'next_before': str(rows[49]['work_id']) if len(rows)>50 else None}

    def records(self, account_id, work_id, kind, after=0):
        with UnitOfWork(self.database) as uow:
            self.repo.get(uow, account_id, work_id)
            if kind == 'events':
                rows = uow.execute('SELECT * FROM work_events WHERE account_id=%s AND work_id=%s '
                                   'AND event_seq>%s ORDER BY event_seq LIMIT 100',
                                   (account_id, work_id, after)).fetchall()
            elif kind == 'runs':
                rows = uow.execute('SELECT * FROM runs WHERE account_id=%s AND work_id=%s '
                                   'ORDER BY run_id DESC LIMIT 100', (account_id, work_id)).fetchall()
            else:
                raise ValueError('unsupported records')
            return {'items': dto(rows)}

    @retryable_transaction
    def accept(self, account_id: UUID, key: str, title: str, requirement: Requirement, *,
               conversation_id: UUID | None = None, source_message_id: UUID | None = None):
        value = requirement.to_dict()
        title = title.strip()
        if not 1 <= len(title) <= 200:
            raise ValueError('invalid title')
        payload = {'title': title, 'requirement': value, 'conversation_id': dto(conversation_id),
                   'source_message_id': dto(source_message_id)}
        with UnitOfWork(self.database) as uow:
            replay, command = self._claim(uow, account_id, 'accept_work', key, payload)
            if replay:
                return replay
            if not AccountRepository().require_active(uow, account_id):
                raise ResourceNotFound()
            if source_message_id and not uow.execute(
                'SELECT 1 FROM messages WHERE account_id=%s AND message_id=%s '
                'AND (%s::uuid IS NULL OR conversation_id=%s)',
                (account_id, source_message_id, conversation_id, conversation_id),
            ).fetchone():
                raise ResourceNotFound()
            work_id = uuid7()
            next_step = (continuation('at_time', due_at=value['timing']['due_at'])
                         if value['timing']['kind']=='once' else continuation())
            uow.execute('INSERT INTO works(work_id,account_id,title,continuation) VALUES (%s,%s,%s,%s::jsonb)',
                        (work_id, account_id, title, json.dumps(next_step)))
            self.repo.insert_requirement(uow, account_id, work_id, 1, value, command, 'accepted', source_message_id)
            work = self.repo.get(uow, account_id, work_id)
            if conversation_id:
                self._link(uow, work, conversation_id, command, source_message_id)
            self.repo.event(uow, work, 'accepted', command_id=command, status='active')
            self.repo.wakeup(uow, work, f'accepted:{command}', 'accepted', 'accepted', next_step.get('due_at'))
            return self._complete(uow, account_id, 'accept_work', key, work_id, 201)

    @staticmethod
    def _link(uow, work, conversation_id, command, source_message_id=None):
        if not uow.execute('SELECT 1 FROM conversations WHERE account_id=%s AND conversation_id=%s',
                           (work['account_id'], conversation_id)).fetchone():
            raise ResourceNotFound()
        uow.execute('INSERT INTO work_conversations(account_id,work_id,conversation_id,'
                    'linked_by_command_id,source_message_id) VALUES (%s,%s,%s,%s,%s) '
                    'ON CONFLICT DO NOTHING',
                    (work['account_id'], work['work_id'], conversation_id, command, source_message_id))

    def _locked(self, uow, account_id, work_id, row_version):
        work = self.repo.get(uow, account_id, work_id, lock=True)
        if work['row_version'] != row_version:
            raise WorkConflict(work)
        return work

    @staticmethod
    def _cancel_coordinator(uow, work):
        run_id = work['active_coordinator_run_id']
        if run_id:
            run = uow.execute('SELECT * FROM runs WHERE account_id=%s AND run_id=%s FOR UPDATE',
                              (work['account_id'], run_id)).fetchone()
            if run['status'] in {'queued', 'running'}:
                uow.execute('UPDATE runs SET status=\'cancelling\',version=version+1,updated_at=now() '
                            'WHERE run_id=%s', (run_id,))
            from persistence.repositories import OutboxRepository

            OutboxRepository().enqueue(uow, uuid7(), work['account_id'], 'cancel_run',
                                       f'cancel-run:{run_id}', None, run_id,
                                       json.dumps({'run_id': str(run_id), 'version': 1}))
        uow.execute('UPDATE work_wakeups SET state=\'superseded\' WHERE work_id=%s AND state=\'pending\'',
                    (work['work_id'],))

    @retryable_transaction
    def revise(self, account_id, work_id, key, row_version, requirement: Requirement, reason='user_revision'):
        value = requirement.to_dict()
        with UnitOfWork(self.database) as uow:
            replay, command = self._claim(uow, account_id, 'revise_work', key,
                                          {'work_id': str(work_id), 'row_version': row_version,
                                           'requirement': value, 'reason': reason})
            if replay:
                return replay
            work = self._locked(uow, account_id, work_id, row_version)
            if work['status'] not in {'active','pausing','paused'}:
                raise WorkConflict(work, 'work_not_revisable')
            revision = work['current_requirement_revision'] + 1
            self.repo.insert_requirement(uow, account_id, work_id, revision, value, command, reason)
            self._cancel_coordinator(uow, work)
            checkpoint = {**work['checkpoint'], 'requirement_revision': revision, 'needs_review': True,
                          'checkpoint_version': work['checkpoint']['checkpoint_version']+1}
            next_step = (continuation('at_time', due_at=value['timing']['due_at'])
                         if value['timing']['kind']=='once' else continuation('ready', 'requirement_revised'))
            if work['continuation'].get('operation_ref'):
                next_step = work['continuation']
            work = dict(uow.execute('UPDATE works SET current_requirement_revision=%s,control_epoch=control_epoch+1,'
                                    'row_version=row_version+1,updated_at=now(),checkpoint=%s::jsonb,continuation=%s::jsonb '
                                    'WHERE work_id=%s RETURNING *',
                                    (revision, json.dumps(checkpoint), json.dumps(next_step), work_id)).fetchone())
            self.repo.event(uow, work, 'revised', command_id=command, reason=reason)
            if work['status']=='active':
                self.repo.wakeup(uow, work, f'revision:{command}', 'revision', 'reevaluate_after_convergence')
            return self._complete(uow, account_id, 'revise_work', key, work_id)

    @retryable_transaction
    def control(self, account_id, work_id, key, row_version, action):
        if action not in {'pause','resume','stop'}:
            raise ValueError('unsupported control')
        operation = f'{action}_work'
        with UnitOfWork(self.database) as uow:
            replay, command = self._claim(uow, account_id, operation, key,
                                          {'work_id': str(work_id), 'row_version': row_version})
            if replay:
                return replay
            work = self._locked(uow, account_id, work_id, row_version)
            allowed = {'pause': {'active'}, 'resume': {'paused'}, 'stop': {'active','pausing','paused'}}
            if work['status'] not in allowed[action]:
                raise WorkConflict(work, 'invalid_work_transition')
            unresolved = bool(work['active_coordinator_run_id'] or work['continuation'].get('operation_ref'))
            status = {'pause': 'pausing' if unresolved else 'paused',
                      'stop': 'stopping' if unresolved else 'stopped', 'resume': 'active'}[action]
            self._cancel_coordinator(uow, work)
            work = dict(uow.execute('UPDATE works SET status=%s,control_epoch=control_epoch+1,'
                                    'row_version=row_version+1,updated_at=now(),'
                                    'stopped_at=CASE WHEN %s=\'stopped\' THEN now() ELSE NULL END '
                                    'WHERE work_id=%s RETURNING *', (status, status, work_id)).fetchone())
            event = {'pausing':'pause_requested','paused':'paused','stopping':'stop_requested',
                     'stopped':'stopped','active':'resumed'}[status]
            self.repo.event(uow, work, event, command_id=command, status=status)
            if action=='resume':
                self.repo.wakeup(uow, work, f'resume:{command}', 'resume', 'explicit_resume')
            return self._complete(uow, account_id, operation, key, work_id)

    @retryable_transaction
    def link(self, account_id, work_id, conversation_id, key, row_version):
        with UnitOfWork(self.database) as uow:
            replay, command = self._claim(uow, account_id, 'link_work', key,
                                          {'work_id': str(work_id), 'conversation_id': str(conversation_id),
                                           'row_version': row_version})
            if replay:
                return replay
            work = self._locked(uow, account_id, work_id, row_version)
            self._link(uow, work, conversation_id, command)
            if work['status'] not in {'stopped', 'completed'}:
                work = dict(uow.execute('UPDATE works SET row_version=row_version+1,updated_at=now() '
                                        'WHERE work_id=%s RETURNING *', (work_id,)).fetchone())
            self.repo.event(uow, work, 'linked', command_id=command, conversation_id=conversation_id)
            return self._complete(uow, account_id, 'link_work', key, work_id)

    @retryable_transaction
    def advance(self, account_id, work_id, key, row_version):
        with UnitOfWork(self.database) as uow:
            replay, command = self._claim(uow, account_id, 'advance_work', key,
                                          {'work_id': str(work_id), 'row_version': row_version})
            if replay:
                return replay
            work = self._locked(uow, account_id, work_id, row_version)
            if work['status']!='active' or work['active_coordinator_run_id']:
                raise WorkConflict(work, 'work_coordinator_busy' if work['active_coordinator_run_id'] else 'work_not_active')
            if not AccountRepository().require_active(uow, account_id):
                raise ResourceNotFound()
            requirement = self.repo.requirement(uow, account_id, work_id, work['current_requirement_revision'])
            if work['continuation']['kind'] in {'awaiting_input','awaiting_delivery'}:
                raise WorkConflict(work, 'work_awaiting_receipt')
            if work['continuation'].get('due_at') and datetime.fromisoformat(work['continuation']['due_at']) > datetime.now(UTC):
                return self._complete(uow, account_id, 'advance_work', key, work_id, 202, reason='not_due')
            wakeup = self.repo.wakeup(uow, work, f'advance:{command}', 'advance', 'explicit_advance')
            from run_domain.admission import admit_work_run

            run = admit_work_run(uow, work, requirement, wakeup, self.database, self.budget_mode)
            work = self.repo.get(uow, account_id, work_id)
            self.repo.event(uow, work, 'advanced', command_id=command, run_id=run['run_id'])
            return self._complete(uow, account_id, 'advance_work', key, work_id, 202, run=run)
