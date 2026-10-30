"""Registered attempt receipts and cross-Run effect references.

Late ingestion writes only the producing operation and its immutable receipt.
It grants no resource, Workspace, transcript, notification or Work progress rights.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.execution import (
    StaleFencingToken,
    assert_execution_fence,
    lock_execution,
    require_active_run,
    require_owned_record,
)
from run_domain.execution_context import execution_fence


class OperationUncertain(RuntimeError):
    code = "tool_side_effect_uncertain"


def begin_operation(uow, operation_id, run_id, operation_type, *, allow_unresolved=False):
    """Enter an Operation in the caller's transaction, preserving its producing attempt.

    An unresolved intent may only enter the tool reconciliation path. Reentry in
    the same fence does not invent an attempt with no registered producer.
    """
    assert_execution_fence(uow)
    fence = execution_fence.get()
    if fence is not None and fence[1] != run_id:
        raise StaleFencingToken("operation is outside the Execution context")
    inserted = uow.execute(
        "INSERT INTO execution_operations(operation_id,run_id,operation_type,execution_id) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (operation_id) DO NOTHING RETURNING operation_id",
        (operation_id, UUID(run_id), operation_type, UUID(fence[2]) if fence else None),
    ).fetchone()
    if inserted is not None:
        ResultReceiptService.register_attempt(uow, operation_id, fence)
        return "started", None
    require_owned_record(uow, "execution_operations", "operation_id", operation_id)
    row = uow.execute(
        "SELECT run_id,operation_type,status,result_payload FROM execution_operations "
        "WHERE operation_id=%s FOR UPDATE", (operation_id,),
    ).fetchone()
    if str(row["run_id"]) != run_id or row["operation_type"] != operation_type:
        raise ValueError("operation_id identity mismatch")
    status = row["status"]
    payload = dict(row["result_payload"]) if row["result_payload"] is not None else None
    if status == "completed":
        return status, payload
    unresolved = status in {"intent_recorded", "uncertain"}
    if unresolved and not allow_unresolved:
        raise OperationUncertain("reconcile the original tool intent before executing again")
    registered = fence is not None and uow.execute(
        "SELECT 1 FROM execution_operation_attempts WHERE operation_id=%s AND fencing_token=%s",
        (operation_id, fence[3]),
    ).fetchone()
    uow.execute(
        "UPDATE execution_operations SET attempt_count=attempt_count+%s,updated_at=now() "
        "WHERE operation_id=%s", (0 if registered else 1, operation_id),
    )
    if not unresolved:
        uow.execute(
            "UPDATE execution_operations SET status='started',error_code=NULL,"
            "result_payload=NULL WHERE operation_id=%s", (operation_id,),
        )
    ResultReceiptService.register_attempt(uow, operation_id, fence)
    return (status, payload) if unresolved else ("started", None)


def result_digest(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).digest()


class ResultReceiptService:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def register_attempt(uow, operation_id, fence):
        if fence is None:
            return
        account_id, run_id, execution_id, token = fence
        operation = uow.execute(
            "SELECT attempt_count FROM execution_operations WHERE operation_id=%s", (operation_id,)
        ).fetchone()
        uow.execute(
            "INSERT INTO execution_operation_attempts(account_id,run_id,execution_id,operation_id,"
            "attempt_no,fencing_token,receipt_ref) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(operation_id,fencing_token) DO NOTHING",
            (
                UUID(account_id),
                UUID(run_id),
                UUID(execution_id),
                operation_id,
                operation["attempt_count"],
                token,
                f"operation:{operation_id}",
            ),
        )

    @retryable_transaction
    def receive(self, account_id, run_id, execution_id, operation_id, token, payload):
        from run_domain.lifecycle import RunLifecycleService

        with UnitOfWork(self.database) as uow:
            run = RunLifecycleService.lock(uow, UUID(account_id), UUID(run_id))
            execution = lock_execution(uow, account_id, run_id, execution_id)
            attempt = uow.execute(
                "SELECT a.*,o.requirement_revision,o.attempt_count,o.status AS operation_status "
                "FROM execution_operation_attempts a JOIN execution_operations o USING(operation_id) "
                "WHERE a.account_id=%s AND a.run_id=%s AND a.execution_id=%s AND a.operation_id=%s "
                "AND a.fencing_token=%s FOR UPDATE OF o",
                (UUID(account_id), UUID(run_id), UUID(execution_id), operation_id, token),
            ).fetchone()
            if attempt is None:
                raise ValueError("receipt has no registered producing attempt")
            disposition = "current"
            if not uow.execute(
                "SELECT 1 FROM accounts WHERE account_id=%s AND status='active'",
                (UUID(account_id),),
            ).fetchone():
                disposition = "stale"
            if run["source_kind"] == "work":
                work = uow.execute(
                    "SELECT * FROM works WHERE work_id=%s", (run["work_id"],)
                ).fetchone()
                if work["current_requirement_revision"] != run["requirement_revision"]:
                    disposition = "stale"
                elif work["status"] != "active":
                    disposition = "cancelled"
                elif work["control_epoch"] != run["work_control_epoch"]:
                    disposition = "stale"
                elif work["active_coordinator_run_id"] != run["run_id"]:
                    disposition = "stale"
            if disposition == "current" and run["status"] in {"cancelling", "cancelled"}:
                disposition = "cancelled"
            if disposition == "current" and execution["status"] in {"cancelling", "cancelled"}:
                disposition = "cancelled"
            lease = uow.execute(
                "SELECT 1 FROM execution_attempt_leases WHERE execution_id=%s AND fencing_token=%s "
                "AND lease_expires_at>clock_timestamp()",
                (UUID(execution_id), token),
            ).fetchone()
            if disposition == "current" and (
                lease is None or run["status"] not in {"queued", "running"}
                or execution["status"] not in {"queued", "running"}
            ):
                disposition = "stale"
            digest = result_digest(payload)
            prior = uow.execute(
                "SELECT * FROM execution_result_receipts WHERE operation_id=%s AND attempt_no=%s",
                (operation_id, attempt["attempt_no"]),
            ).fetchone()
            if prior:
                if bytes(prior["digest"]) != digest:
                    raise ValueError("receipt payload conflict")
                return prior["disposition"]
            uow.execute(
                "INSERT INTO execution_result_receipts(account_id,run_id,execution_id,operation_id,"
                "attempt_no,requirement_revision,result_ref,digest,disposition) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    UUID(account_id),
                    UUID(run_id),
                    UUID(execution_id),
                    operation_id,
                    attempt["attempt_no"],
                    attempt["requirement_revision"],
                    attempt["receipt_ref"],
                    digest,
                    disposition,
                ),
            )
            # Retain a late result in its original operation only when a replacement
            # attempt has not already taken it over. Otherwise retain digest/provenance.
            if (
                disposition != "current"
                and (run["status"] in {"cancelling", "cancelled", "failed", "succeeded"}
                     or execution and execution["status"] in {"cancelling", "cancelled", "failed"})
                and (attempt["attempt_no"] == attempt["attempt_count"])
                and (attempt["operation_status"] != "completed")
            ):
                uow.execute(
                    "UPDATE execution_operations SET status='completed',result_ref=%s,result_payload=%s,"
                    "completed_at=now(),updated_at=now(),error_code=NULL WHERE operation_id=%s",
                    (attempt["receipt_ref"], Jsonb(payload), operation_id),
                )
            return disposition


class EffectService:
    def __init__(self, database):
        self.database = database

    @retryable_transaction
    def register(self, account_id, run_id, execution_id, operation_id, effect_key, parameters):

        with UnitOfWork(self.database) as uow:
            run = require_active_run(uow, account_id, run_id)
            lock_execution(uow, account_id, run_id, execution_id)
            assert_execution_fence(uow)
            if run["source_kind"] != "work":
                raise ValueError("cross-Run effects require Work ownership")
            digest = result_digest(parameters)
            prior = uow.execute(
                "SELECT * FROM execution_operations WHERE account_id=%s AND work_id=%s "
                "AND effect_key=%s FOR UPDATE",
                (UUID(account_id), run["work_id"], effect_key),
            ).fetchone()
            if prior:
                if bytes(prior["parameters_digest"]) != digest:
                    raise ValueError("effect key parameter conflict")
                if prior["run_id"] != UUID(run_id):
                    uow.execute(
                        "INSERT INTO execution_effect_references(account_id,run_id,execution_id,"
                        "operation_id,producing_run_id,producing_execution_id,requirement_revision) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (
                            UUID(account_id),
                            UUID(run_id),
                            UUID(execution_id),
                            prior["operation_id"],
                            prior["run_id"],
                            prior["execution_id"],
                            run["requirement_revision"],
                        ),
                    )
                # Caller must reconcile intent_recorded/uncertain before any dispatch.
                return dict(prior)
            uow.execute(
                "INSERT INTO execution_operations(operation_id,account_id,run_id,execution_id,"
                "operation_type,effect_key,parameters_digest) VALUES (%s,%s,%s,%s,'tool',%s,%s)",
                (
                    operation_id,
                    UUID(account_id),
                    UUID(run_id),
                    UUID(execution_id),
                    effect_key,
                    digest,
                ),
            )
            return dict(
                uow.execute(
                    "SELECT * FROM execution_operations WHERE operation_id=%s", (operation_id,)
                ).fetchone()
            )


def complete_operation(
    uow: UnitOfWork, operation_id: str, result_ref: str, result_payload: dict[str, Any]
) -> None:
    assert_execution_fence(uow)
    require_owned_record(uow, "execution_operations", "operation_id", operation_id)
    prior = uow.execute(
        "SELECT status,result_ref,result_payload FROM execution_operations "
        "WHERE operation_id=%s FOR UPDATE",
        (operation_id,),
    ).fetchone()
    if prior is None:
        raise ValueError("operation not found")
    if prior["status"] == "completed":
        if prior["result_ref"] != result_ref or prior["result_payload"] != result_payload:
            raise ValueError("completed operation result conflict")
        return
    fence = execution_fence.get()
    if fence is not None:

        attempt = uow.execute(
            "SELECT * FROM execution_operation_attempts WHERE operation_id=%s "
            "AND fencing_token=%s",
            (operation_id, fence[3]),
        ).fetchone()
        if attempt is None:
            raise ValueError("result attempt was not registered")
        receipt = uow.execute(
            "SELECT digest,disposition FROM execution_result_receipts "
            "WHERE operation_id=%s AND attempt_no=%s",
            (operation_id, attempt["attempt_no"]),
        ).fetchone()
        if receipt is not None:
            if bytes(receipt["digest"]) != result_digest(result_payload):
                raise ValueError("receipt payload conflict")
            if receipt["disposition"] != "current":
                raise StaleFencingToken("a late receipt cannot become current execution evidence")
        else:
            uow.execute(
                "INSERT INTO execution_result_receipts(account_id,run_id,execution_id,operation_id,"
                "attempt_no,requirement_revision,result_ref,digest,disposition) "
                "SELECT account_id,run_id,execution_id,operation_id,%s,requirement_revision,%s,%s,'current' "
                "FROM execution_operations WHERE operation_id=%s",
                (
                    attempt["attempt_no"],
                    attempt["receipt_ref"],
                    result_digest(result_payload),
                    operation_id,
                ),
            )
    uow.execute(
        "UPDATE execution_operations SET status='completed' ,result_ref=%s,result_payload=%s,"
        "error_code=NULL,completed_at=now(),updated_at=now() WHERE operation_id=%s",
        (result_ref, Jsonb(result_payload), operation_id),
    )
