"""Registered attempt receipts and cross-Run effect references.

Late ingestion writes only the producing operation and its immutable receipt.
It grants no resource, Workspace, transcript, notification or Work progress rights.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from psycopg.types.json import Jsonb

from persistence.uow import UnitOfWork, retryable_transaction


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
            if disposition == "current" and run["status"] in {"cancelling", "cancelled"}:
                disposition = "cancelled"
            lease = uow.execute(
                "SELECT 1 FROM execution_attempt_leases WHERE execution_id=%s AND fencing_token=%s "
                "AND lease_expires_at>clock_timestamp()",
                (UUID(execution_id), token),
            ).fetchone()
            if disposition == "current" and (
                lease is None or run["status"] not in {"queued", "running"}
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
            execution = uow.execute("SELECT status FROM run_executions WHERE account_id=%s AND run_id=%s AND execution_id=%s",
                (UUID(account_id), UUID(run_id), UUID(execution_id))).fetchone()
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
        from agent_activities.store import AgentDataStore

        with UnitOfWork(self.database) as uow:
            run = AgentDataStore._active_run(uow, account_id, run_id)
            AgentDataStore._execution(uow, account_id, run_id, execution_id)
            AgentDataStore._assert_fence(uow)
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
