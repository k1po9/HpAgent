"""SQL for mandate identity, immutable requirements and business events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from uuid6 import uuid7

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound
from work_domain.models import digest
from work_domain.timing import next_daily


def dto(value: Any) -> Any:
    if isinstance(value, (UUID, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {key: dto(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [dto(item) for item in value]
    return value


class WorkRepository:
    @staticmethod
    def get(uow: UnitOfWork, account_id: UUID, work_id: UUID, *, lock: bool = False) -> dict:
        row = uow.execute('SELECT * FROM works WHERE account_id=%s AND work_id=%s' +
                          (' FOR UPDATE' if lock else ''), (account_id, work_id)).fetchone()
        if row is None:
            raise ResourceNotFound()
        return dict(row)

    @staticmethod
    def requirement(uow: UnitOfWork, account_id: UUID, work_id: UUID, revision: int) -> dict:
        row = uow.execute('SELECT * FROM work_requirements WHERE account_id=%s AND work_id=%s '
                          'AND revision=%s', (account_id, work_id, revision)).fetchone()
        if row is None:
            raise ResourceNotFound()
        return dict(row)

    @staticmethod
    def insert_requirement(uow: UnitOfWork, account_id: UUID, work_id: UUID, revision: int,
                           value: dict, command_id: UUID, reason: str,
                           source_message_id: UUID | None = None) -> None:
        uow.execute(
            'INSERT INTO work_requirements(account_id,work_id,revision,objective,constraints,'
            'acceptance_criteria,completion_mode,capability_key,spec,timing,resource_requests,'
            'deliverable_policy,created_by,source_message_id,command_id,change_reason,content_hash) '
            'VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,'
            '%s::jsonb,%s,%s,%s,%s,%s)',
            (account_id, work_id, revision, value['objective'], json.dumps(value['constraints']),
             json.dumps(value['acceptance_criteria']), value['completion_mode'], value['capability_key'],
             json.dumps(value['spec']), json.dumps(value['timing']), json.dumps(value['resource_requests']),
             json.dumps(value['deliverable_policy']), 'account', source_message_id, command_id,
             reason, digest(value)))

    @staticmethod
    def event(uow: UnitOfWork, work: dict, event_type: str, *, command_id: UUID | None = None,
              run_id: UUID | None = None, **payload: Any) -> None:
        event_id = uuid7()
        uow.execute(
            'INSERT INTO work_events(event_id,account_id,work_id,event_seq,event_type,'
            'requirement_revision,command_id,run_id,bounded_payload) '
            'SELECT %s,%s,%s,COALESCE(max(event_seq),0)+1,%s,%s,%s,%s,%s::jsonb '
            'FROM work_events WHERE account_id=%s AND work_id=%s',
            (event_id, work['account_id'], work['work_id'], event_type,
             work['current_requirement_revision'], command_id, run_id,
             json.dumps({'schema_version': 1, **dto(payload)}), work['account_id'], work['work_id']))
        uow.execute(
            'INSERT INTO outbox_events(outbox_event_id,account_id,event_type,business_key,'
            'work_id,aggregate_type,aggregate_id,payload) VALUES (%s,%s,\'publish_work_event\','
            '%s,%s,\'work\',%s,%s::jsonb)',
            (uuid7(), work['account_id'], f'work-event:{event_id}', work['work_id'], work['work_id'],
             json.dumps({'work_id': str(work['work_id']), 'event_id': str(event_id), 'version': 1})))

    @staticmethod
    def wakeup(uow: UnitOfWork, work: dict, key: str, kind: str, reason: str,
               due_at: str | None = None) -> UUID:
        row = uow.execute(
            'INSERT INTO work_wakeups(wakeup_id,account_id,work_id,trigger_key,kind,'
            'expected_revision,reason,due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz,now())) '
            'ON CONFLICT(work_id,trigger_key) DO UPDATE SET trigger_key=EXCLUDED.trigger_key '
            'RETURNING wakeup_id,expected_revision,kind,reason',
            (uuid7(), work['account_id'], work['work_id'], key, kind,
             work['current_requirement_revision'], reason, due_at)).fetchone()
        if (row['expected_revision'], row['kind'], row['reason']) != (
            work['current_requirement_revision'], kind, reason
        ):
            from web_domain.errors import IdempotencyConflict

            raise IdempotencyConflict()
        return row['wakeup_id']


def sync_schedule(uow, work, requirement):
    timing = requirement['timing']
    row = uow.execute('SELECT * FROM work_schedules WHERE account_id=%s AND work_id=%s FOR UPDATE',
                      (work['account_id'], work['work_id'])).fetchone()
    enabled = work['status'] == 'active' and timing['kind'] != 'immediate'
    changed = row is None or row['timing'] != timing or row['desired_enabled'] != enabled
    version = (row['schedule_version'] + int(changed)) if row else 1
    due = row['next_due_at'] if row and not changed else None
    if enabled and changed:
        due = (datetime.fromisoformat(timing['due_at']) if timing['kind'] == 'once'
               else next_daily(timing, datetime.now(UTC)))
        # Existing one-shot enqueue is never repeated on resume after receipt.
        if timing['kind'] == 'once' and work['continuation']['kind'] == 'awaiting_delivery':
            due = None
    uow.execute('INSERT INTO work_schedules(schedule_id,account_id,work_id,requirement_revision,'
                'schedule_version,desired_enabled,timing,timezone,next_due_at,source_row_version) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) '
                'ON CONFLICT(account_id,work_id) DO UPDATE SET requirement_revision=EXCLUDED.requirement_revision,'
                'schedule_version=EXCLUDED.schedule_version,desired_enabled=EXCLUDED.desired_enabled,'
                'timing=EXCLUDED.timing,timezone=EXCLUDED.timezone,next_due_at=EXCLUDED.next_due_at,'
                'source_row_version=EXCLUDED.source_row_version',
                (uuid7(), work['account_id'], work['work_id'], work['current_requirement_revision'],
                 version, enabled, json.dumps(timing), timing['timezone'], due, work['row_version']))
