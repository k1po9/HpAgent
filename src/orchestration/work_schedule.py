"""PG schedule projection and bounded due recovery; no live Work workflow."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from persistence.uow import UnitOfWork, retryable_transaction
from resources.run_budget import RunBudgetExhausted
from run_domain.admission import admit_work_run
from run_domain.lifecycle import RunLifecycleService
from web_domain.errors import DomainError
from work_domain.models import continuation
from work_domain.persistence import WorkRepository, sync_schedule
from work_domain.timing import latest_daily, next_daily

logger = logging.getLogger(__name__)




class WorkScheduleService:
    def __init__(self, database, *, budget_mode='enforce'):
        self.database = database
        self.budget_mode = budget_mode

    @retryable_transaction
    def occurrence(self, account_id, work_id, version, scheduled_for, *, now=None):
        """Stable callback API; disabled/old versions leave a skipped fact."""
        now = now or datetime.now(UTC)
        if scheduled_for.tzinfo is None or scheduled_for > now:
            raise ValueError('occurrence must be an aware due UTC instant')
        with UnitOfWork(self.database) as uow:
            work = WorkRepository.get(uow, account_id, work_id, lock=True)
            schedule = uow.execute('SELECT * FROM work_schedules WHERE account_id=%s AND work_id=%s '
                                   'FOR UPDATE', (account_id, work_id)).fetchone()
            if schedule is None:
                raise ValueError('schedule unavailable')
            return self._occurrence(uow, work, schedule, version, scheduled_for, now)

    def _occurrence(self, uow, work, schedule, version, scheduled_for, now, missed_from=None):
        prior = uow.execute('SELECT * FROM work_schedule_occurrences WHERE schedule_id=%s '
                            'AND schedule_version=%s AND scheduled_for=%s',
                            (schedule['schedule_id'], version, scheduled_for)).fetchone()
        if prior:
            return dict(prior)
        valid = (schedule['desired_enabled'] and schedule['schedule_version'] == version
                 and work['status'] == 'active')
        if valid:
            timing = schedule['timing']
            expected = (datetime.fromisoformat(timing['due_at']) if timing['kind'] == 'once'
                        else latest_daily(timing, scheduled_for))
            if expected != scheduled_for:
                raise ValueError('occurrence does not match schedule')
        disposition = 'pending' if valid else 'skipped'
        reason = 'due' if valid else 'disabled_or_superseded'
        wakeup = None
        if valid:
            # Daily catch-up retains at most the newest pending occurrence.
            if schedule['timing']['kind'] == 'daily':
                uow.execute("UPDATE work_wakeups SET state='skipped' WHERE account_id=%s AND work_id=%s "
                            "AND kind='due' AND state='pending'", (work['account_id'], work['work_id']))
            key = f"schedule:{schedule['schedule_id']}:{version}:{scheduled_for.astimezone(UTC).isoformat()}"
            wakeup = WorkRepository.wakeup(uow, work, key, 'due', 'scheduled_occurrence', scheduled_for.isoformat())
        row = uow.execute('INSERT INTO work_schedule_occurrences(account_id,work_id,schedule_id,'
                          'schedule_version,scheduled_for,wakeup_id,disposition,reason,missed_from) '
                          'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                          (work['account_id'], work['work_id'], schedule['schedule_id'], version,
                           scheduled_for, wakeup, disposition, reason, missed_from)).fetchone()
        if valid:
            due = (next_daily(schedule['timing'], now) if schedule['timing']['kind'] == 'daily' else None)
            uow.execute('UPDATE work_schedules SET next_due_at=%s,last_evaluated_at=%s WHERE schedule_id=%s',
                        (due, now, schedule['schedule_id']))
            # Awaiting receipts/input are never cleared by a timer.
            if work['continuation']['kind'] in {'ready', 'at_time'}:
                uow.execute('UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=now() WHERE work_id=%s',
                            (json.dumps(continuation('ready', 'scheduled_occurrence')), work['work_id']))
        return dict(row)

    @retryable_transaction
    def run_once(self, limit=50, *, now=None):
        now = now or datetime.now(UTC)
        with UnitOfWork(self.database) as uow:
            # Reconcile lost/startup synchronization, then evaluate due projections.
            rows = uow.execute('SELECT w.* FROM works w LEFT JOIN work_schedules s USING(account_id,work_id) '
                               'WHERE s.schedule_id IS NULL OR s.source_row_version<>w.row_version '
                               'OR s.applied_version<>s.schedule_version ORDER BY w.work_id '
                               'LIMIT %s FOR UPDATE OF w SKIP LOCKED', (limit,)).fetchall()
            for work in rows:
                requirement = WorkRepository.requirement(uow, work['account_id'], work['work_id'], work['current_requirement_revision'])
                sync_schedule(uow, work, requirement)
                uow.execute('UPDATE work_schedules SET applied_version=schedule_version WHERE work_id=%s',
                            (work['work_id'],))
            rows = uow.execute('SELECT w.* FROM works w JOIN work_schedules s USING(account_id,work_id) '
                               'WHERE s.desired_enabled AND s.next_due_at<=%s ORDER BY s.next_due_at '
                               'LIMIT %s FOR UPDATE OF w SKIP LOCKED', (now, limit)).fetchall()
            for work in rows:
                schedule = uow.execute('SELECT * FROM work_schedules WHERE work_id=%s FOR UPDATE',
                                       (work['work_id'],)).fetchone()
                due = schedule['next_due_at']
                latest = latest_daily(schedule['timing'], now) if schedule['timing']['kind'] == 'daily' else due
                self._occurrence(uow, work, schedule, schedule['schedule_version'], latest, now,
                                 due if latest > due else None)
        return self.dispatch_due(limit, now=now)

    @retryable_transaction
    def dispatch_due(self, limit=50, *, now=None):
        now = now or datetime.now(UTC)
        count = 0
        with UnitOfWork(self.database) as uow:
            rows = uow.execute("WITH eligible AS (SELECT w.work_id,w.account_id,row_number() OVER (PARTITION BY w.account_id ORDER BY w.work_id) AS tenant_position "
                               "FROM works w WHERE w.status='active' AND w.active_coordinator_run_id IS NULL AND EXISTS "
                               "(SELECT 1 FROM work_wakeups x WHERE x.work_id=w.work_id AND x.state='pending' AND x.due_at<=%s)) "
                               "SELECT w.* FROM eligible e JOIN works w USING(work_id,account_id) LEFT JOIN capacity_turns t "
                               "ON t.account_id=w.account_id AND t.resource='coordination' AND t.lane='background' "
                               "ORDER BY e.tenant_position,COALESCE(t.last_admitted_at,'-infinity'::timestamptz),w.work_id "
                               "LIMIT %s FOR UPDATE OF w SKIP LOCKED", (now,limit)).fetchall()
            for work in rows:
                kind = work['continuation']['kind']
                due = work['continuation'].get('due_at')
                if kind not in {'ready', 'at_time', 'retry_after'} or (due and datetime.fromisoformat(due) > now):
                    continue
                if RunLifecycleService.unresolved_effect(uow, work) or not uow.execute(
                    "SELECT 1 FROM accounts WHERE account_id=%s AND status='active'", (work['account_id'],)
                ).fetchone():
                    continue
                requirement = WorkRepository.requirement(uow, work['account_id'], work['work_id'], work['current_requirement_revision'])
                wake = uow.execute("SELECT * FROM work_wakeups WHERE work_id=%s AND expected_revision=%s "
                                   "AND state='pending' AND due_at<=%s ORDER BY due_at DESC,created_at DESC,wakeup_id DESC LIMIT 1 FOR UPDATE",
                                   (work['work_id'], work['current_requirement_revision'], now)).fetchone()
                if wake is None:
                    continue
                # Keep failures local to one Work; other tenants' wakeups still progress.
                try:
                    with uow.connection.transaction():
                        run = admit_work_run(uow, work, requirement, wake['wakeup_id'], self.database, self.budget_mode)
                        if run is None:
                            continue
                        uow.execute("INSERT INTO capacity_turns(resource,lane,account_id,last_admitted_at) VALUES ('coordination','background',%s,now()) ON CONFLICT(resource,lane,account_id) DO UPDATE SET last_admitted_at=now()", (work['account_id'],))
                        current = WorkRepository.get(uow, work['account_id'], work['work_id'])
                        WorkRepository.event(uow, current, 'advanced', run_id=run['run_id'])
                    count += 1
                except (ValueError, DomainError, RunBudgetExhausted) as exc:
                    logger.warning('Work admission blocked: %s', type(exc).__name__)
                    uow.execute('UPDATE works SET continuation=%s::jsonb,row_version=row_version+1 WHERE work_id=%s',
                                (json.dumps(continuation('blocked', 'budget_exhausted' if isinstance(exc, RunBudgetExhausted) else 'admission_rejected')), work['work_id']))
                except Exception:
                    # The savepoint rolls back this admission only. Keep its wakeup
                    # pending for recovery and let other accounts make progress.
                    logger.exception('Work admission failed: work_id=%s', work['work_id'])
        return count


async def run_work_schedule_loop(service, interval_seconds=2):
    while True:
        try:
            await asyncio.to_thread(service.run_once)
        except Exception:
            logger.exception('Work scheduler iteration failed; retrying next iteration')
        await asyncio.sleep(interval_seconds)
