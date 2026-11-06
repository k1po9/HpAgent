"""IdempotencyRepository SQL operations in the caller-owned transaction."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from .uow import UnitOfWork


class IdempotencyRepository:
    def claim(
        self,
        uow: UnitOfWork,
        command_id: UUID,
        account_id: UUID,
        operation: str,
        key: str,
        request_hash: bytes,
        expires_at: Any,
    ) -> dict[str, Any] | None:
        inserted = uow.execute(
            "INSERT INTO idempotency_commands(idempotency_command_id,account_id,operation,"
            "idempotency_key,request_hash,expires_at) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (account_id,operation,idempotency_key) DO NOTHING "
            "RETURNING idempotency_command_id",
            (command_id, account_id, operation, key, request_hash, expires_at),
        ).fetchone()
        if inserted:
            return None
        return cast(
            dict[str, Any] | None,
            uow.execute(
                "SELECT * FROM idempotency_commands WHERE account_id=%s AND operation=%s "
                "AND idempotency_key=%s FOR UPDATE",
                (account_id, operation, key),
            ).fetchone(),
        )

    def complete(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation: str,
        key: str,
        response_status: int,
        body: str,
    ) -> None:
        uow.execute(
            "UPDATE idempotency_commands SET status='completed',response_status=%s,"
            "response_body=%s::jsonb,completed_at=now() WHERE account_id=%s "
            "AND operation=%s AND idempotency_key=%s",
            (response_status, body, account_id, operation, key),
        )
