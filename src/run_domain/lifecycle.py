"""Single transaction boundary for Run state and owner-specific projections."""

from __future__ import annotations

import json

from persistence.repositories import AccountRepository
from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.models import TERMINAL
from web_domain.errors import ConversationBusy, ResourceNotFound
from work_domain.models import bounded, continuation
from work_domain.persistence import WorkRepository


class RunLifecycleService:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def lock(uow, account_id, run_id):
        row = uow.execute('SELECT * FROM runs WHERE account_id=%s AND run_id=%s',
                          (account_id, run_id)).fetchone()
        if row is None:
            raise ResourceNotFound()
        if row['source_kind']=='work':
            WorkRepository.get(uow, account_id, row['work_id'], lock=True)
        else:
            if not uow.execute('SELECT 1 FROM conversations WHERE account_id=%s AND conversation_id=%s '
                               'FOR UPDATE', (account_id, row['conversation_id'])).fetchone():
                raise ResourceNotFound()
        return dict(uow.execute('SELECT * FROM runs WHERE account_id=%s AND run_id=%s FOR UPDATE',
                                (account_id, run_id)).fetchone())

    @staticmethod
    def check_work(uow, run):
        if not AccountRepository().require_active(uow, run['account_id']):
            raise ResourceNotFound()
        work = WorkRepository.get(uow, run['account_id'], run['work_id'])
        if work['status']!='active' or work['active_coordinator_run_id']!=run['run_id'] or (
            work['current_requirement_revision']!=run['requirement_revision'] or
            work['control_epoch']!=run['work_control_epoch']
        ):
            raise ConversationBusy()
        return work

    @staticmethod
    def unresolved_effect(uow, work):
        return uow.execute("SELECT operation_id FROM execution_operations WHERE account_id=%s AND work_id=%s "
                           "AND status IN ('intent_recorded','uncertain') AND "
                           "COALESCE(result_payload->>'side_effect_class','unknown') IN ('non_idempotent_write','unknown') "
                           "ORDER BY started_at,operation_id LIMIT 1", (work['account_id'], work['work_id'])).fetchone()

    @retryable_transaction
    def start(self, account_id, run_id):
        with UnitOfWork(self.database) as uow:
            run = self.lock(uow, account_id, run_id)
            if run['status'] not in {'queued','running'}:
                return False
            if run['source_kind']=='work':
                self.check_work(uow, run)
            if run['status']=='queued':
                uow.execute('UPDATE runs SET status=\'running\',started_at=GREATEST(now(),created_at),'
                            'version=version+1,updated_at=now() WHERE run_id=%s', (run_id,))
            return True

    @retryable_transaction
    def finish(self, account_id, run_id, status, *, content=None, result=None, failure_code=None,
               failure_message=None, accept_result=False):
        with UnitOfWork(self.database) as uow:
            run = self.lock(uow, account_id, run_id)
            return self.finish_in_uow(uow, run, status, content=content, result=result,
                                      failure_code=failure_code, failure_message=failure_message,
                                      accept_result=accept_result)

    def finish_in_uow(self, uow, run, status, *, content=None, result=None, failure_code=None,
                      failure_message=None, accept_result=False):
        if status not in TERMINAL:
            raise ValueError('invalid terminal status')
        if run['status'] in TERMINAL:
            if result is not None and run['result_json'] != result:
                raise ValueError('terminal execution result cannot be changed')
            return run['status']==status
        if status=='succeeded' and run['status']!='running':
            raise ConversationBusy()
        if status=='failed' and run['status']=='cancelling':
            return False
        if status=='cancelled' and run['status']=='running':
            raise ConversationBusy()
        work = None
        if run['source_kind']=='work':
            work = WorkRepository.get(uow, run['account_id'], run['work_id'])
            if status=='succeeded':
                work = self.check_work(uow, run)
                if result is None:
                    raise ValueError('Work execution requires a typed result')
                bounded(result, {'schema_version','kind','evidence','continuation','checkpoint'})
                if result.get('kind') not in {'deliverable_ready','progress_saved','waiting_input',
                                              'waiting_due','notification_enqueued'}:
                    raise ValueError('invalid execution result kind')
                for item in result.get('evidence', []):
                    if set(item) != {'criterion_id','type','ref'} or not item['ref']:
                        raise ValueError('invalid execution evidence')
                if 'checkpoint' in result:
                    checkpoint = result['checkpoint']
                    bounded(checkpoint, {'schema_version','checkpoint_version','requirement_revision',
                                        'source_run_id','facts','decisions','unresolved','next_steps','evidence_refs'})
                    if checkpoint['requirement_revision'] != run['requirement_revision'] or (
                        checkpoint['source_run_id'] != str(run['run_id']) or
                        checkpoint['checkpoint_version'] != work['checkpoint']['checkpoint_version']+1
                    ):
                        raise ValueError('checkpoint provenance/version mismatch')
        uow.execute('UPDATE runs SET status=%s,failure_code=%s,failure_message=%s,'
                    'result_json=%s::jsonb,finished_at=GREATEST(now(),created_at,COALESCE(started_at,created_at)),'
                    'version=version+1,updated_at=now() WHERE run_id=%s',
                    (status, failure_code, failure_message, json.dumps(result) if result else None, run['run_id']))
        run = dict(uow.execute('SELECT * FROM runs WHERE run_id=%s', (run['run_id'],)).fetchone())
        if work is None:
            from conversation_domain.run_projection import project_terminal

            project_terminal(uow, run, status, content)
            return True
        next_step = continuation('blocked', 'attempt_cancelled' if status=='cancelled' else 'attempt_failed')
        if status=='succeeded':
            raw = result.get('continuation', continuation('ready','execution_succeeded'))
            next_step = continuation(raw['kind'], raw['reason'], **{k:v for k,v in raw.items()
                                                                 if k not in {'kind','reason','schema_version'}})
        unresolved = self.unresolved_effect(uow, work)
        if unresolved:
            next_step = continuation('blocked', 'side_effect_uncertain', operation_ref=unresolved['operation_id'])
        target_status = work['status']
        if unresolved:
            pass
        elif work['continuation'].get('operation_ref'):
            next_step = work['continuation']
        elif run['requirement_revision'] != work['current_requirement_revision']:
            next_step = work['continuation']
        if target_status in {'pausing','stopping'} and not next_step.get('operation_ref'):
            target_status = {'pausing':'paused','stopping':'stopped'}[target_status]
        checkpoint = result.get('checkpoint', work['checkpoint']) if status=='succeeded' else work['checkpoint']
        work = dict(uow.execute('UPDATE works SET active_coordinator_run_id=NULL,status=%s,'
                                'continuation=%s::jsonb,checkpoint=%s::jsonb,row_version=row_version+1,'
                                'control_epoch=control_epoch+CASE WHEN %s IN (\'paused\',\'stopped\') THEN 1 ELSE 0 END,'
                                'stopped_at=CASE WHEN %s=\'stopped\' THEN now() ELSE NULL END,updated_at=now() '
                                'WHERE work_id=%s AND active_coordinator_run_id=%s RETURNING *',
                                (target_status, json.dumps(next_step), json.dumps(checkpoint), target_status,
                                 target_status, work['work_id'], run['run_id'])).fetchone())
        WorkRepository.event(uow, work, f'run_{status}', run_id=run['run_id'], status=target_status)
        if accept_result and status=='succeeded':
            from work_domain.completion import WorkCompletionPolicy

            WorkCompletionPolicy.accept(uow, work, run)
        return True

    @retryable_transaction
    def converge_controls(self):
        with UnitOfWork(self.database) as uow:
            rows = uow.execute("SELECT * FROM works WHERE status IN ('pausing','stopping') "
                               "AND active_coordinator_run_id IS NULL ORDER BY work_id FOR UPDATE SKIP LOCKED").fetchall()
            for work in rows:
                if self.unresolved_effect(uow, work):
                    continue
                # Only a confirmed operation receipt permits control convergence.
                ref = work['continuation'].get('operation_ref')
                if ref and not uow.execute("SELECT 1 FROM execution_operations o JOIN execution_result_receipts r "
                                           "ON r.operation_id=o.operation_id WHERE o.account_id=%s AND o.work_id=%s "
                                           "AND o.operation_id=%s AND o.status='completed'",
                                           (work['account_id'], work['work_id'], ref)).fetchone():
                    continue
                status = 'paused' if work['status'] == 'pausing' else 'stopped'
                work = dict(uow.execute("UPDATE works SET status=%s,control_epoch=control_epoch+1,"
                                        "row_version=row_version+1,updated_at=now(),continuation=%s::jsonb,"
                                        "stopped_at=CASE WHEN %s='stopped' THEN now() ELSE NULL END "
                                        "WHERE work_id=%s RETURNING *",
                                        (status, json.dumps(continuation('none', 'control_converged')),
                                         status, work['work_id'])).fetchone())
                WorkRepository.event(uow, work, status, status=status)
