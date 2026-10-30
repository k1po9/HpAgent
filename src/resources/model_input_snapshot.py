"""Durable, append-only model request snapshots."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from psycopg.types.json import Jsonb
from uuid6 import uuid7

from persistence.uow import UnitOfWork, retryable_transaction
from resources.model_client import MODEL_REQUEST_SERIALIZER_VERSION, PreparedModelRequest

SNAPSHOT_HASH_VERSION = "model-input-sha256-v2"


def canonicalize_resolved_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("resolved provider URL must be absolute http(s)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("resolved provider URL must not contain credentials, query, or fragment")
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", "", ""))


@dataclass(frozen=True)
class ModelInputSnapshotRef:
    snapshot_id: UUID
    content_hash: bytes
    model_call_id: UUID
    fallback_attempt: int


def semantic_request_hash(
    *, endpoint_id: str, provider: str, model: str, api_format: str, resolved_url: str,
    payload: Mapping[str, Any],
    serializer_version: str = MODEL_REQUEST_SERIALIZER_VERSION,
) -> bytes:
    binding = {
        "hash_version": SNAPSHOT_HASH_VERSION,
        "serializer_version": serializer_version,
        "endpoint_id": endpoint_id,
        "provider": provider,
        "model": model,
        "api_format": api_format,
        "resolved_url": canonicalize_resolved_url(resolved_url),
        "provider_request_body": dict(payload),
    }
    return hashlib.sha256(json.dumps(
        binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).digest()


def snapshot_content_hash(prepared: PreparedModelRequest) -> bytes:
    return semantic_request_hash(
        endpoint_id=prepared.endpoint_id, provider=prepared.provider,
        model=prepared.model, api_format=prepared.api_format,
        resolved_url=prepared.url,
        payload=prepared.body(), serializer_version=prepared.serializer_version,
    )


class SnapshotConflict(RuntimeError):
    pass


class SnapshotRepository:
    def __init__(self, database: object):
        self.database = database

    @retryable_transaction
    def freeze(
        self, *, account_id: UUID, run_id: UUID, model_call_id: UUID,
        operation_id: str, execution_attempt: int, call_ordinal: int,
        fallback_attempt: int, phase: str, endpoint_id: str, provider: str,
        model: str, api_format: str, resolved_url: str, payload: Mapping[str, Any],
        entitlement_version: int,
        serializer_version: str = MODEL_REQUEST_SERIALIZER_VERSION,
    ) -> ModelInputSnapshotRef:
        digest = semantic_request_hash(
            endpoint_id=endpoint_id, provider=provider, model=model,
            api_format=api_format, resolved_url=resolved_url, payload=payload,
            serializer_version=serializer_version,
        )
        return self._insert(
            account_id=account_id, run_id=run_id, model_call_id=model_call_id,
            operation_id=operation_id, execution_attempt=execution_attempt,
            call_ordinal=call_ordinal, fallback_attempt=fallback_attempt,
            phase=phase, endpoint_id=endpoint_id, provider=provider, model=model,
            api_format=api_format, resolved_url=canonicalize_resolved_url(resolved_url), payload=payload, digest=digest,
            serializer_version=serializer_version,
            entitlement_version=entitlement_version,
        )

    def create(
        self, *, account_id: UUID, run_id: UUID, model_call_id: UUID,
        operation_id: str, execution_attempt: int, call_ordinal: int,
        fallback_attempt: int, phase: str, entitlement_version: int,
        prepared: PreparedModelRequest,
    ) -> ModelInputSnapshotRef:
        prepared.verify_immutable()
        return self.freeze(
            account_id=account_id, run_id=run_id, model_call_id=model_call_id,
            operation_id=operation_id, execution_attempt=execution_attempt,
            call_ordinal=call_ordinal, fallback_attempt=fallback_attempt,
            phase=phase, endpoint_id=prepared.endpoint_id,
            provider=prepared.provider, model=prepared.model,
            api_format=prepared.api_format, resolved_url=prepared.url, payload=prepared.body(),
            serializer_version=prepared.serializer_version,
            entitlement_version=entitlement_version,
        )

    def _insert(
        self, *, account_id: UUID, run_id: UUID, model_call_id: UUID,
        operation_id: str, execution_attempt: int, call_ordinal: int,
        fallback_attempt: int, phase: str, endpoint_id: str, provider: str,
        model: str, api_format: str, resolved_url: str, payload: Mapping[str, Any], digest: bytes,
        serializer_version: str, entitlement_version: int,
    ) -> ModelInputSnapshotRef:
        with UnitOfWork(self.database) as uow:
            owns = uow.execute(
                "SELECT 1 FROM runs WHERE run_id=%s AND account_id=%s",
                (run_id, account_id),
            ).fetchone()
            if owns is None:
                raise LookupError("Run does not belong to model-call Account")
            from run_domain.execution_context import execution_fence
            fence = execution_fence.get()
            from persistence.memory_context import check_execution
            manifest = check_execution(uow, account_id, run_id, fence[2] if fence else None)
            row = uow.execute(
                "INSERT INTO model_input_snapshots(snapshot_id,account_id,run_id,"
                "model_call_id,operation_id,execution_attempt,call_ordinal,fallback_attempt,"
                "phase,endpoint_id,provider,model,api_format,resolved_url,provider_request_body,"
                "content_hash,serializer_version,entitlement_version,execution_id,memory_context) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(account_id,run_id,operation_id) DO NOTHING RETURNING *",
                (uuid7(), account_id, run_id, model_call_id, operation_id,
                 execution_attempt, call_ordinal, fallback_attempt, phase,
                 endpoint_id, provider, model, api_format, resolved_url, Jsonb(dict(payload)), digest,
                 serializer_version, entitlement_version, UUID(fence[2]) if fence else None, Jsonb(manifest)),
            ).fetchone()
            if row is None:
                row = uow.execute(
                    "SELECT * FROM model_input_snapshots WHERE account_id=%s AND run_id=%s "
                    "AND operation_id=%s", (account_id, run_id, operation_id),
                ).fetchone()
            if row is None or bytes(row["content_hash"]) != digest:
                raise SnapshotConflict("snapshot operation replay changed semantic request")
            return ModelInputSnapshotRef(
                row["snapshot_id"], bytes(row["content_hash"]),
                row["model_call_id"], row["fallback_attempt"],
            )

    def get(self, account_id: UUID, snapshot_id: UUID) -> Mapping[str, Any] | None:
        with UnitOfWork(self.database) as uow:
            return uow.execute(
                "SELECT * FROM model_input_snapshots WHERE account_id=%s AND snapshot_id=%s",
                (account_id, snapshot_id),
            ).fetchone()

    def mark_dispatched(self, account_id, snapshot_id):
        with UnitOfWork(self.database) as uow:
            from persistence.memory_context import account_epoch, check_execution, check_manifest
            from run_domain.execution import (
                StaleFencingToken,
                assert_execution_fence,
                require_active_run,
            )
            from run_domain.execution_context import execution_fence
            account_epoch(uow, account_id)
            assert_execution_fence(uow)
            snapshot = uow.execute('SELECT run_id,execution_id,memory_context,operation_id,entitlement_version FROM model_input_snapshots WHERE account_id=%s AND snapshot_id=%s', (account_id, snapshot_id)).fetchone()
            if snapshot is None:
                raise LookupError('snapshot missing')
            require_active_run(uow, str(account_id), str(snapshot['run_id']))
            fence = execution_fence.get()
            snapshot_execution = snapshot['execution_id']
            if snapshot_execution is None:
                snapshot_execution = uow.execute(
                    "SELECT execution_id FROM run_executions WHERE account_id=%s AND run_id=%s "
                    "AND role='root'", (account_id, snapshot['run_id']),
                ).fetchone()['execution_id']
            if fence is not None and (
                str(account_id) != fence[0] or str(snapshot['run_id']) != fence[1]
                or str(snapshot_execution) != fence[2]
            ):
                raise StaleFencingToken('snapshot is outside the Execution context')
            check_execution(uow, account_id, snapshot['run_id'], snapshot['execution_id'])
            check_manifest(uow, account_id, snapshot['memory_context'])
            from account.entitlement_service import EntitlementState
            from resources.account_daily_budget import (
                AccountDailyBudgetConflict,
                AccountDailyBudgetService,
                AccountModelEntitlementUnavailable,
            )

            budget = AccountDailyBudgetService(self.database)
            lookup = budget.entitlements.get_in_uow(uow, account_id)
            if (lookup.state is not EntitlementState.VALID or lookup.entitlement is None
                    or lookup.entitlement.version != snapshot['entitlement_version']):
                raise AccountModelEntitlementUnavailable('model entitlement changed before dispatch')
            _, ledger = budget._locked_ledger(
                uow, account_id, snapshot['operation_id'], None,
            )
            if ledger['state'] != 'reserved':
                raise AccountDailyBudgetConflict('model dispatch requires its original reservation')
            inserted = uow.execute(
                'INSERT INTO model_dispatches(account_id,snapshot_id) VALUES (%s,%s) '
                'ON CONFLICT DO NOTHING RETURNING snapshot_id', (account_id,snapshot_id),
            ).fetchone()
            if inserted is None:
                raise AccountDailyBudgetConflict('model request already dispatched; recover its original Operation')
