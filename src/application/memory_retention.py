"""Chat admission and durable Hindsight Retain advancement (no workflow I/O)."""
from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import UUID

from memory.hindsight_client import (
    HindsightContractError,
    HindsightRejected,
    HindsightUnavailable,
    HindsightUncertain,
)
from memory.scope_policy import MemoryScopeError, chat_scope, memory_scope
from persistence.memory import MemoryRequestConflict, MemoryStore
from persistence.uow import UnitOfWork

POLICY_VERSION = 'chat-v2'
# Explicit opt-outs/secrets are never uploaded. High-impact assertions need
# evidence before retention; ordinary knowledge does not require review.
_OPT_OUT = re.compile(r"不要(?:记住|记录|保存)|别记住|不许记|do not (?:remember|store)|don't (?:remember|store)", re.I)
_SECRET = re.compile(r"-----BEGIN .*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b|(?:密码|口令|password|api[_ -]?key|access[_ -]?token|secret)\s*[:：=]\s*\S+", re.I)
_HIGH_IMPACT = re.compile(r"(?:确诊|诊断为|药物剂量|停药|有罪|犯罪记录|信用评分|银行账号|身份证号)|\b(?:diagnosed with|dosage|convicted of|credit score|bank account)\b", re.I)


def admission(user: str, assistant: str) -> str:
    if _OPT_OUT.search(user) or _SECRET.search(user + '\n' + assistant):
        return 'reject'
    if _HIGH_IMPACT.search(user + '\n' + assistant):
        return 'evidence_required'
    return 'allow'


class MemoryRetentionService:
    def __init__(self, database_url: object, hindsight_client: Any):
        self._database_url = database_url
        self._hindsight = hindsight_client
        self.store = MemoryStore(database_url)

    def load_completed_run(self, run_id: UUID) -> dict[str, Any] | None:
        """读取 Run + trigger user Message + produced assistant Message。

        校验（doc §36）:
          run.status == 'succeeded'
          assistant.status == 'completed' 且 content 非空
          trigger.role == 'user' 且 trigger.status == 'accepted'
          account_id / conversation_id 由 join 天然一致

        任何一条不满足 → 返回 None（failed/cancelled/running/queued 都拒绝，
        doc §39）。
        """
        with UnitOfWork(self._database_url) as uow:
            row = uow.execute(
                "SELECT r.run_id, r.account_id, r.conversation_id, r.session_id,"
                "r.status AS run_status,"
                "t.role AS trigger_role, t.status AS trigger_status,"
                "t.content AS trigger_content,t.origin,"
                "a.status AS assistant_status, a.content AS assistant_content, t.created_at AS source_time "
                "FROM runs r "
                "JOIN messages t ON t.account_id=r.account_id "
                "AND t.conversation_id=r.conversation_id "
                "AND t.message_id=r.trigger_message_id "
                "JOIN messages a ON a.account_id=r.account_id "
                "AND a.conversation_id=r.conversation_id "
                "AND a.produced_by_run_id=r.run_id AND a.role='assistant' "
                "WHERE r.run_id=%s",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        if row["run_status"] != "succeeded":
            return None
        if row["assistant_status"] != "completed":
            return None
        if not row["assistant_content"]:
            return None
        if row["trigger_role"] != "user" or row["trigger_status"] != "accepted":
            return None
        return row


    def prepare(self, event: dict, worker_id: str) -> None:
        run = self.load_completed_run(event['run_id'])
        payload: dict = {}
        tags: list[str] = []
        decision = 'reject'
        if run is not None:
            origin = run.get('origin') or {}
            surface = origin.get('channel_type', 'web')
            document_id = f"{'web-run' if surface == 'web' else 'chat-run'}:{run['run_id']}"
            try:
                policy = chat_scope(str(run['account_id']), origin)
                tags = list(policy.tags)
            except MemoryScopeError:
                # Unknown scopes have no authorized source shape; finish without upload.
                run = None
            if run is not None:
                decision = admission(run['trigger_content'], run['assistant_content'])
                payload = {'document_id': document_id}
                if decision == 'allow':
                    payload.update(
                        source_context=('Chat source. User text is a user assertion. Assistant text is an '
                                        'unconfirmed assistant response: suggestions, predictions and proposals '
                                        'are not confirmed user decisions or established facts.'),
                        events=[{'role': 'user', 'content': run['trigger_content']},
                                {'role': 'assistant', 'content': run['assistant_content']}],
                        user_id=str(run['account_id']),
                        iso_timestamp=run['source_time'].isoformat(),
                        channel_type=surface,
                        scope='group' if 'scope:group' in tags else 'private',
                        group_id=origin.get('context_key', '') if len(tags) == 2 else '',
                        metadata={str(k): str(v) for k, v in {
                            'source': surface, 'run_id': run['run_id'],
                            'conversation_id': run['conversation_id'], 'session_id': run['session_id'],
                            'external_message_id': origin.get('external_message_id'),
                            'bot_id': origin.get('bot_id'), 'room_id': origin.get('room_id'),
                            'thread_id': origin.get('thread_id'),
                            'policy_version': POLICY_VERSION,
                            'user_role': 'user_assertion',
                            'assistant_role': 'unconfirmed_assistant_response',
                        }.items() if v is not None},
                    )
                else:
                    payload['admission'] = decision  # no rejected plaintext retained
        snapshot = getattr(self._hindsight, 'quality_snapshot', lambda: None)()
        self.store.register(event, worker_id, run, payload, tags, decision,
                            quality_snapshot=snapshot)

    async def process_event(self, event: dict, worker_id: str) -> None:
        if event['event_type'] == 'retain_memory':
            try:
                await asyncio.to_thread(self.prepare, event, worker_id)
            except MemoryRequestConflict:
                await asyncio.to_thread(self.store.reject_event, event, worker_id, 'memory_request_conflict')
            return
        action = await asyncio.to_thread(self.store.load_action, event, worker_id)
        if action['status'] in {'verified', 'failed'}:
            # Re-delivery acknowledges the existing fact without rewriting its audit.
            await asyncio.to_thread(self.store.finish_event, event, worker_id)
            return
        if action['kind'] != 'retain':
            from application.memory_governance import MemoryGovernanceWorker
            await MemoryGovernanceWorker(self).advance(event, worker_id, action)
            return
        if action['source_state'] == 'forgotten':
            await asyncio.to_thread(self.store.save, event, worker_id, action, 'failed',
                                    error_code='memory_source_blocked')
            return
        permitted = (action['account_active'] and action['retention_permitted'] and action['source_state'] == 'active' and not action['manual_curation']
                     and action['source_generation'] == action['generation']
                     and action['current_action_id'] == action['action_id'])
        if action['status'] == 'queued' and not permitted:
            await asyncio.to_thread(self.store.save, event, worker_id, action, 'failed',
                                    error_code='memory_source_blocked')
            return
        account, operation = str(action['account_id']), str(action['operation_id'])
        status = action['status']
        remote_status = None
        try:
            # Query first on every takeover, including a crash before local ack.
            remote = await self._hindsight.get_operation(account, operation)
            if remote.get('operation_id') != operation:
                raise HindsightContractError('hindsight_operation_identity_mismatch')
            remote_status = remote['status']
            if remote_status == 'not_found':
                if not permitted:
                    await asyncio.to_thread(self.store.save, event, worker_id, action, 'uncertain',
                                            error_code='memory_source_blocked')
                    return
                prepare = getattr(self._hindsight, 'prepare_retain', None)
                if prepare is not None:
                    await prepare(account, action['request_payload'].get('quality_snapshot'))
                async with self.store.sender_guard(action['source_id']) as acquired:
                    if not acquired:
                        await asyncio.to_thread(self.store.save, event, worker_id, action, 'uncertain',
                                                error_code='memory_sender_active')
                        return
                    await asyncio.to_thread(self.store.mark_sending, event, worker_id, action)
                    status = 'submitted'
                    try:
                        receipt = await self._hindsight.retain_document(
                            **action['request_payload'], async_retain=True, operation_id=operation)
                    except HindsightRejected:
                        await asyncio.to_thread(self.store.send_state, action, 'settled')
                        raise
                    except BaseException:
                        await asyncio.to_thread(self.store.send_state, action, 'unknown')
                        raise
                    await asyncio.to_thread(self.store.send_state, action, 'settled')
                if not receipt.accepted or receipt.operation_id != operation or not receipt.async_processing:
                    raise HindsightContractError('hindsight_retain_not_accepted')
                await asyncio.to_thread(self.store.save, event, worker_id, action, 'submitted')
                return
            if remote_status in {'pending', 'processing'}:
                status = 'submitted'
            elif remote_status == 'cancelled':
                await asyncio.to_thread(self.store.save, event, worker_id, action, 'failed',
                                        error_code='hindsight_operation_cancelled')
                return
            elif remote_status == 'failed':
                if not permitted or action['failure_count'] >= 8:
                    await asyncio.to_thread(self.store.save, event, worker_id, action, 'failed',
                                            error_code='hindsight_operation_failed')
                    return
                prepare = getattr(self._hindsight, 'prepare_retain', None)
                if prepare is not None:
                    await prepare(account, action['request_payload'].get('quality_snapshot'))
                async with self.store.sender_guard(action['source_id']) as acquired:
                    if not acquired:
                        raise HindsightContractError('memory_sender_active')
                    await asyncio.to_thread(self.store.mark_sending, event, worker_id, action)
                    try:
                        await self._hindsight.retry_operation(account, operation)
                    except BaseException:
                        await asyncio.to_thread(self.store.send_state, action, 'unknown')
                        raise
                    await asyncio.to_thread(self.store.send_state, action, 'settled')
                await asyncio.to_thread(self.store.save, event, worker_id, action, 'submitted',
                                        error_code='hindsight_operation_retry', failure=True)
                return
            elif remote_status == 'completed':
                status = 'verifying'
                await asyncio.to_thread(self.store.mark_verifying, event, worker_id, action)
                document = await self._hindsight.get_document(account, action['document_id'])
                document = document if isinstance(document, dict) else document.to_dict()
                payload = action['request_payload']
                policy = memory_scope(account, payload['scope'], payload['group_id'])
                if (document.get('bank_id') != policy.bank_id
                        or document.get('id') != action['document_id']
                        or not policy.matches_document(document)):
                    raise HindsightContractError('hindsight_document_mismatch')
                verification = {'document_id': action['document_id'],
                                'operation_id': operation, 'scope': list(policy.tags)}
                # These are native document extraction counts, not Retain items_count.
                # Some older/fake targets omit them: absence is unknown, never zero.
                if 'memory_unit_count' in document:
                    verification['memory_unit_count'] = document['memory_unit_count']
                if 'nodes_by_fact_type' in document:
                    verification['nodes_by_fact_type'] = document['nodes_by_fact_type']
                await asyncio.to_thread(self.store.save, event, worker_id, action, 'verified',
                                        verification=verification)
                return
            else:
                raise HindsightContractError('hindsight_operation_status_unknown')
            await asyncio.to_thread(self.store.save, event, worker_id, action, status)
        except HindsightRejected as exc:
            # Native retry may race a preceding retry's acceptance. Re-query it.
            retry_conflict = exc.status == 409 and remote_status == 'failed'
            await asyncio.to_thread(self.store.save, event, worker_id, action,
                                    'uncertain' if retry_conflict else 'failed',
                                    error_code=exc.code, failure=not retry_conflict)
        except (HindsightUncertain, HindsightContractError) as exc:
            await asyncio.to_thread(self.store.save, event, worker_id, action, 'uncertain',
                                    error_code=exc.code, failure=True,
                                    delay=min(60, 2 ** min(action['failure_count'], 6)))
        except HindsightUnavailable as exc:
            await asyncio.to_thread(self.store.save, event, worker_id, action, status,
                                    error_code=exc.code, failure=True,
                                    delay=min(60, 2 ** min(action['failure_count'], 6)))
