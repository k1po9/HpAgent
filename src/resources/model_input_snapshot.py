"""Durable, append-only model request snapshots."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID

from psycopg.types.json import Jsonb
from uuid6 import uuid7

from persistence.uow import UnitOfWork, retryable_transaction
from resources.model_client import MODEL_REQUEST_SERIALIZER_VERSION, PreparedModelRequest

SNAPSHOT_HASH_VERSION = "model-input-sha256-v1"


@dataclass(frozen=True)
class ModelInputSnapshotRef:
    snapshot_id: UUID
    content_hash: bytes
    model_call_id: UUID
    fallback_attempt: int


def semantic_request_hash(
    *, endpoint_id: str, provider: str, model: str, api_format: str,
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
        "provider_request_body": dict(payload),
    }
    return hashlib.sha256(json.dumps(
        binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).digest()


def snapshot_content_hash(prepared: PreparedModelRequest) -> bytes:
    return semantic_request_hash(
        endpoint_id=prepared.endpoint_id, provider=prepared.provider,
        model=prepared.model, api_format=prepared.api_format,
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
        model: str, api_format: str, payload: Mapping[str, Any],
        entitlement_version: int,
        serializer_version: str = MODEL_REQUEST_SERIALIZER_VERSION,
    ) -> ModelInputSnapshotRef:
        digest = semantic_request_hash(
            endpoint_id=endpoint_id, provider=provider, model=model,
            api_format=api_format, payload=payload,
            serializer_version=serializer_version,
        )
        return self._insert(
            account_id=account_id, run_id=run_id, model_call_id=model_call_id,
            operation_id=operation_id, execution_attempt=execution_attempt,
            call_ordinal=call_ordinal, fallback_attempt=fallback_attempt,
            phase=phase, endpoint_id=endpoint_id, provider=provider, model=model,
            api_format=api_format, payload=payload, digest=digest,
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
            api_format=prepared.api_format, payload=prepared.body(),
            serializer_version=prepared.serializer_version,
            entitlement_version=entitlement_version,
        )

    def _insert(
        self, *, account_id: UUID, run_id: UUID, model_call_id: UUID,
        operation_id: str, execution_attempt: int, call_ordinal: int,
        fallback_attempt: int, phase: str, endpoint_id: str, provider: str,
        model: str, api_format: str, payload: Mapping[str, Any], digest: bytes,
        serializer_version: str, entitlement_version: int,
    ) -> ModelInputSnapshotRef:
        with UnitOfWork(self.database) as uow:
            owns = uow.execute(
                "SELECT 1 FROM runs WHERE run_id=%s AND account_id=%s",
                (run_id, account_id),
            ).fetchone()
            if owns is None:
                raise LookupError("Run does not belong to model-call Account")
            row = uow.execute(
                "INSERT INTO model_input_snapshots(snapshot_id,account_id,run_id,"
                "model_call_id,operation_id,execution_attempt,call_ordinal,fallback_attempt,"
                "phase,endpoint_id,provider,model,api_format,provider_request_body,"
                "content_hash,serializer_version,entitlement_version) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(account_id,run_id,operation_id) DO NOTHING RETURNING *",
                (uuid7(), account_id, run_id, model_call_id, operation_id,
                 execution_attempt, call_ordinal, fallback_attempt, phase,
                 endpoint_id, provider, model, api_format, Jsonb(dict(payload)), digest,
                 serializer_version, entitlement_version),
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
