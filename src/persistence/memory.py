"""Short transactions for source registration, action fencing and read permission."""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from persistence.memory_context import account_epoch
from persistence.repositories import OutboxRepository
from persistence.uow import UnitOfWork
from web_domain.errors import OutboxLeaseLost


def request_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


class MemoryRequestConflict(ValueError):
    """An existing source/action key cannot accept a different frozen request."""


class MemoryStore:
    def __init__(self, database_url: object):
        self.database_url = database_url
        self.outbox = OutboxRepository()

    @asynccontextmanager
    async def sender_guard(self, source_id):
        # A session advisory lock survives commits but is released by process death.
        # There is no open transaction while awaiting Hindsight.
        uow = await asyncio.to_thread(UnitOfWork, self.database_url)
        key = int.from_bytes(hashlib.sha256(str(source_id).encode()).digest()[:8], 'big', signed=True)
        acquired = False
        try:
            row = await asyncio.to_thread(lambda: uow.execute(
                'SELECT pg_try_advisory_lock(%s) AS acquired', (key,)).fetchone())
            acquired = row['acquired']
            await asyncio.to_thread(uow.connection.commit)
            yield acquired
        finally:
            if acquired:
                await asyncio.to_thread(uow.execute, 'SELECT pg_advisory_unlock(%s)', (key,))
            await asyncio.to_thread(uow.__exit__, None, None, None)

    def send_state(self, action, state):
        with UnitOfWork(self.database_url) as uow:
            uow.execute("UPDATE memory_actions SET send_uncertain=send_uncertain OR %s='unknown',send_state=%s WHERE account_id=%s AND action_id=%s",
                        (state, state, action['account_id'], action['action_id']))

    def progress(self, action, evidence):
        with UnitOfWork(self.database_url) as uow:
            uow.execute('UPDATE memory_actions SET verification=%s::jsonb WHERE account_id=%s AND action_id=%s',
                        (json.dumps(evidence), action['account_id'], action['action_id']))

    def owned(self, uow: UnitOfWork, event: dict, worker_id: str):
        row = self.outbox.lock_owned_event(uow, event['outbox_event_id'], worker_id)
        # attempt_count is the existing monotonically increasing delivery fence,
        # including when a process reuses its worker_id after lease recovery.
        if row is None or row['attempt_count'] != event['attempt_count']:
            raise OutboxLeaseLost()
        return row

    def register(self, event: dict, worker_id: str, run: dict | None,
                 payload: dict | None, scope: list[str], decision: str,
                 quality_snapshot: dict | None = None) -> None:
        with UnitOfWork(self.database_url) as uow:
            account_epoch(uow, event['account_id'])
            row = self.owned(uow, event, worker_id)
            if run is not None:
                if row['account_id'] != run['account_id'] or row['run_id'] != run['run_id']:
                    raise ValueError('memory_source_owner_mismatch')
                # Source content identity is independent of the quality profile.
                # Action request identity includes its once-frozen profile.
                digest = request_hash(payload or {'decision': decision})
                if decision == 'allow' and payload is not None:
                    old = uow.execute(
                        'SELECT request_payload FROM memory_actions WHERE account_id=%s AND action_key=%s',
                        (run['account_id'], 'retain:' + str(run['run_id'])),
                    ).fetchone()
                    snapshot = ((old['request_payload'] or {}).get('quality_snapshot')
                                if old is not None else quality_snapshot)
                    if snapshot is not None:
                        payload = {**payload, 'quality_snapshot': snapshot}
                document_id = (payload or {})['document_id']
                blocked = uow.execute(
                    'SELECT memory_retention_blocked FROM runs WHERE account_id=%s AND run_id=%s',
                    (run['account_id'], run['run_id']),
                ).fetchone()['memory_retention_blocked']
                uow.execute(
                    "INSERT INTO memory_sources(source_id,account_id,source_kind,source_key,"
                    "origin_run_id,document_id,scope,source_hash,policy_version,state) "
                    "VALUES (%s,%s,'chat',%s,%s,%s,%s,%s,'chat-v2',%s) "
                    "ON CONFLICT DO NOTHING",
                    (uuid4(), run['account_id'], document_id, run['run_id'], document_id,
                     scope, digest, 'quarantined' if blocked else 'active' if decision == 'allow' else
                     ('blocked' if decision == 'reject' else 'quarantined')),
                )
                source = uow.execute(
                    "SELECT * FROM memory_sources WHERE account_id=%s AND source_kind='chat' "
                    "AND origin_run_id=%s AND source_key=%s FOR UPDATE",
                    (run['account_id'], run['run_id'], document_id),
                ).fetchone()
                if source is None or source['source_hash'] != digest:
                    raise MemoryRequestConflict('memory_request_conflict')
                if not blocked and decision == 'allow' and source['state'] == 'active' and not source['manual_curation']:
                    self.freeze(uow, source, payload, 'retain:' + str(run['run_id']), row)
            if not self.outbox.mark_processed(uow, row['outbox_event_id'], worker_id):
                raise OutboxLeaseLost()

    def finish_event(self, event: dict, worker_id: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            row = self.owned(uow, event, worker_id)
            if not self.outbox.mark_processed(uow, row['outbox_event_id'], worker_id):
                raise OutboxLeaseLost()

    def reject_event(self, event: dict, worker_id: str, code: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            row = self.owned(uow, event, worker_id)
            if not self.outbox.mark_dead_letter(uow, row['outbox_event_id'], worker_id, code, code):
                raise OutboxLeaseLost()

    def freeze(self, uow: UnitOfWork, source: dict, payload: dict | None,
               action_key: str, event: dict) -> dict:
        digest = request_hash(payload or {})
        action = uow.execute(
            "SELECT * FROM memory_actions WHERE account_id=%s AND action_key=%s",
            (source['account_id'], action_key),
        ).fetchone()
        if action:
            if action['request_hash'] != digest or action['source_id'] != source['source_id']:
                raise MemoryRequestConflict('memory_request_conflict')
            return dict(action)
        if source['state'] != 'active' or source['manual_curation']:
            raise ValueError('memory_source_blocked')
        if source['current_action_id'] is not None:
            raise ValueError('memory_source_busy')
        action_id = uuid4()
        action = uow.execute(
            "INSERT INTO memory_actions(action_id,account_id,source_id,generation,kind,actor,"
            "reason,action_key,request_hash,request_payload,operation_id) "
            "VALUES (%s,%s,%s,%s,'retain','system','chat_completed',%s,%s,%s::jsonb,%s) RETURNING *",
            (action_id, source['account_id'], source['source_id'], source['generation'],
             action_key, digest, json.dumps(payload), uuid4()),
        ).fetchone()
        uow.execute("UPDATE memory_sources SET current_action_id=%s,updated_at=now() "
                    "WHERE account_id=%s AND source_id=%s",
                    (action_id, source['account_id'], source['source_id']))
        self.outbox.enqueue(uow, uuid4(), source['account_id'], 'memory_action',
                            'memory-action:' + str(action_id), event['conversation_id'],
                            source['origin_run_id'], json.dumps({'action_id': str(action_id)}))
        return dict(action)

    def load_action(self, event: dict, worker_id: str) -> dict:
        with UnitOfWork(self.database_url) as uow:
            row = self.owned(uow, event, worker_id)
            action = uow.execute(
                "SELECT a.*,s.state AS source_state,s.generation AS source_generation,"
                "s.current_action_id,s.manual_curation,s.document_id,s.scope,(ac.status='active') AS account_active,"
                "NOT r.memory_retention_blocked AS retention_permitted "
                "FROM memory_sources s JOIN accounts ac ON ac.account_id=s.account_id JOIN memory_actions a "
                "ON a.account_id=s.account_id AND a.source_id=s.source_id "
                "JOIN runs r ON r.account_id=s.account_id AND r.run_id=s.origin_run_id "
                "WHERE a.account_id=%s AND a.action_id=%s AND s.origin_run_id=%s FOR UPDATE OF s,a",
                (row['account_id'], row['payload']['action_id'], row['run_id']),
            ).fetchone()
            if action is None:
                raise ValueError('memory_action_missing')
            return dict(action)

    def mark_sending(self, event: dict, worker_id: str, action: dict) -> None:
        with UnitOfWork(self.database_url) as uow:
            account_epoch(uow, action['account_id'])
            self.owned(uow, event, worker_id)
            account = uow.execute('SELECT status FROM accounts WHERE account_id=%s FOR SHARE',
                                  (action['account_id'],)).fetchone()
            if account is None or account['status'] != 'active':
                raise OutboxLeaseLost()
            source = uow.execute("SELECT * FROM memory_sources WHERE account_id=%s "
                                 "AND source_id=%s FOR UPDATE",
                                 (action['account_id'], action['source_id'])).fetchone()
            blocked = uow.execute(
                'SELECT memory_retention_blocked FROM runs WHERE account_id=%s AND run_id=%s',
                (action['account_id'], source['origin_run_id']),
            ).fetchone()['memory_retention_blocked']
            if (blocked or source['state'] != 'active' or source['manual_curation']
                    or source['generation'] != action['generation']
                    or source['current_action_id'] != action['action_id']):
                raise OutboxLeaseLost()
            uow.execute("UPDATE memory_actions SET status='submitted',send_uncertain=send_uncertain OR send_state='sending',send_state='sending',updated_at=now() "
                        "WHERE account_id=%s AND action_id=%s",
                        (action['account_id'], action['action_id']))

    def mark_verifying(self, event: dict, worker_id: str, action: dict) -> None:
        with UnitOfWork(self.database_url) as uow:
            self.owned(uow, event, worker_id)
            uow.execute("UPDATE memory_actions SET status='verifying',updated_at=now() "
                        "WHERE account_id=%s AND action_id=%s",
                        (action['account_id'], action['action_id']))

    def save(self, event: dict, worker_id: str, action: dict, status: str, *,
             error_code: str | None = None, failure: bool = False,
             verification: dict | None = None, delay: int = 2) -> None:
        with UnitOfWork(self.database_url) as uow:
            row = self.owned(uow, event, worker_id)
            source = uow.execute("SELECT * FROM memory_sources WHERE account_id=%s "
                                 "AND source_id=%s FOR UPDATE",
                                 (action['account_id'], action['source_id'])).fetchone()
            current = (source['generation'] == action['generation'] and
                       source['current_action_id'] == action['action_id'])
            # Old external operations still need reconciliation. Their receipts
            # may update that action's audit, never the new source position.
            if status == 'verified' and (not current or (action['kind'] == 'retain' and
                    (source['state'] != 'active' or source['manual_curation']))):
                status, error_code = 'uncertain', 'memory_source_changed'
            terminal = status in {'verified', 'failed'}
            uow.execute(
                "UPDATE memory_actions SET status=%s,error_code=%s,"
                "failure_count=failure_count+%s,verification=COALESCE(%s::jsonb,verification),updated_at=now(),"
                "verified_at=CASE WHEN %s='verified' THEN now() ELSE verified_at END "
                "WHERE account_id=%s AND action_id=%s",
                (status, error_code, int(failure), json.dumps(verification) if verification is not None else None, status,
                 action['account_id'], action['action_id']),
            )
            if status == 'verified' and action['kind'] != 'retain':
                if action['kind'] == 'delete':
                    # Remove local frozen plaintext after external payload cleanup.
                    uow.execute("UPDATE memory_actions SET request_payload=NULL,reason='source_deleted' WHERE account_id=%s AND source_id=%s",
                                (action['account_id'], action['source_id']))
                else:
                    if action['kind'] == 'replace' and action['request_payload']:
                        uow.execute('UPDATE memory_sources SET scope=%s WHERE account_id=%s AND source_id=%s',
                                    (action['request_payload']['new_scope'], action['account_id'], action['source_id']))
                    uow.execute("UPDATE memory_sources SET state='active' WHERE account_id=%s AND source_id=%s",
                                (action['account_id'], action['source_id']))
                    uow.execute('UPDATE memory_actions SET request_payload=NULL WHERE account_id=%s AND action_id=%s',
                                (action['account_id'], action['action_id']))
            if terminal:
                if not self.outbox.mark_processed(uow, row['outbox_event_id'], worker_id):
                    raise OutboxLeaseLost()
            else:
                # A normal wait is not a failure or a dead-letter candidate.
                uow.execute("UPDATE outbox_events SET status='pending',locked_at=NULL,"
                            "locked_by=NULL,available_at=now()+(%s*interval '1 second'),"
                            "last_error_code=%s,last_error_message=NULL,updated_at=now() "
                            "WHERE outbox_event_id=%s", (delay, error_code, row['outbox_event_id']))

    def readable_documents(self, account_id: Any, scope: list[str], documents: set[str]) -> set[str]:
        if not documents:
            return set()
        with UnitOfWork(self.database_url) as uow:
            rows = uow.execute(
                "SELECT s.document_id FROM memory_sources s JOIN memory_actions a "
                "ON a.account_id=s.account_id AND a.source_id=s.source_id "
                "JOIN runs r ON r.account_id=s.account_id AND r.run_id=s.origin_run_id "
                "WHERE a.action_id=s.current_action_id AND NOT r.memory_retention_blocked AND s.account_id=%s AND s.scope=%s "
                "AND s.document_id=ANY(%s) AND s.state='active' AND a.status='verified' AND NOT EXISTS(SELECT 1 FROM memory_sources b JOIN memory_actions c ON c.account_id=b.account_id AND c.action_id=b.current_action_id WHERE b.account_id=s.account_id AND c.kind<>'retain' AND c.status<>'verified' AND (c.affected_scopes IS NULL OR c.affected_scopes='\"account\"'::jsonb OR c.affected_scopes @> jsonb_build_array(to_jsonb(s.scope)))) "
                "AND a.generation=s.generation",
                (account_id, scope, list(documents)),
            ).fetchall()
            return {row['document_id'] for row in rows}
