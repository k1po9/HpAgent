"""Explicit resource references and independent product projections."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from uuid6 import uuid7

from persistence.uow import UnitOfWork, retryable_transaction
from resources.run_budget import _amounts
from web_domain.errors import ResourceNotFound
from work_domain.models import continuation
from work_domain.persistence import WorkRepository, dto


def projections(uow, account, work):
    budget = uow.execute(
        "SELECT limits,used,reserved,version FROM work_budgets WHERE account_id=%s AND work_id=%s",
        (account, work),
    ).fetchone()
    artifacts = uow.execute(
        "SELECT a.*,v.status,v.artifact_id,v.producing_run_id,v.producing_execution_id,v.file_id "
        "FROM work_artifacts a JOIN artifact_versions v USING(account_id,artifact_version_id) "
        "WHERE a.account_id=%s AND a.work_id=%s ORDER BY a.created_at DESC LIMIT 100",
        (account, work),
    ).fetchall()
    deliveries = uow.execute(
        "SELECT d.delivery_id,d.notification_id,d.state,d.next_part,d.attempts,d.provider_receipt,d.last_error,"
        "n.requirement_revision,n.purpose,t.channel,t.audience FROM deliveries d "
        "JOIN notifications n USING(account_id,notification_id) JOIN delivery_targets t USING(account_id,target_id) "
        "WHERE n.account_id=%s AND n.work_id=%s ORDER BY n.created_at DESC LIMIT 100",
        (account, work),
    ).fetchall()
    saves = uow.execute(
        "SELECT run_id,requirement_revision,state,operation_id,entry_id,expected_revision,failure_code FROM research_run_save_intents WHERE account_id=%s AND work_id=%s ORDER BY run_id DESC LIMIT 100",
        (account, work),
    ).fetchall()
    return {
        "workspace_saves": list(saves),
        "budget": dict(budget) if budget else None,
        "artifacts": list(artifacts),
        "deliveries": list(deliveries),
    }


def validate_evidence(uow, work, run, evidence):
    for item in evidence:
        kind, ref = item["type"], item["ref"]
        if kind in {"artifact_version", "research_report"}:
            valid = uow.execute(
                "SELECT 1 FROM artifact_versions WHERE account_id=%s AND artifact_version_id=%s "
                "AND producing_run_id=%s AND status='completed'",
                (work["account_id"], UUID(ref), run["run_id"]),
            ).fetchone()
            if not valid and kind == "artifact_version":
                valid = uow.execute(
                    "SELECT 1 FROM artifact_versions v JOIN work_artifacts a USING(account_id,artifact_version_id) "
                    "WHERE v.account_id=%s AND a.work_id=%s AND a.source_requirement_revision=%s "
                    "AND v.artifact_version_id=%s AND v.status='completed' AND a.role IN ('input','evidence') "
                    "AND %s::jsonb @> jsonb_build_array(jsonb_build_object('artifact_version_id',v.artifact_version_id::text,'role',a.role))",
                    (
                        work["account_id"],
                        work["work_id"],
                        work["current_requirement_revision"],
                        UUID(ref),
                        json.dumps(run["input_snapshot"].get("artifact_refs", [])),
                    ),
                ).fetchone()
        elif kind == "operation_receipt":
            valid = uow.execute(
                "SELECT 1 FROM execution_operations o JOIN execution_result_receipts r USING(operation_id) "
                "WHERE o.account_id=%s AND o.run_id=%s AND (o.operation_id=%s OR o.result_ref=%s) "
                "AND o.status='completed' AND r.disposition='current'",
                (work["account_id"], run["run_id"], ref, ref),
            ).fetchone()
        elif kind == "delivery_receipt":
            valid = uow.execute(
                "SELECT 1 FROM deliveries d JOIN notifications n USING(account_id,notification_id) "
                "JOIN delivery_targets t USING(account_id,target_id) WHERE d.account_id=%s AND d.delivery_id=%s "
                "AND d.state='accepted' AND n.work_id=%s AND n.run_id=%s AND n.requirement_revision=%s "
                "AND n.control_epoch=%s AND n.notification_id::text=%s AND t.enabled AND t.target_version=d.target_version",
                (
                    work["account_id"],
                    UUID(ref),
                    work["work_id"],
                    run["run_id"],
                    work["current_requirement_revision"],
                    work["control_epoch"],
                    work["continuation"].get("receipt_ref"),
                ),
            ).fetchone()
        elif kind == "user_acceptance":
            valid = uow.execute(
                "SELECT 1 FROM work_result_acceptances WHERE account_id=%s AND work_id=%s "
                "AND requirement_revision=%s AND acceptance_id=%s AND run_id=%s",
                (
                    work["account_id"],
                    work["work_id"],
                    work["current_requirement_revision"],
                    UUID(ref),
                    run["run_id"],
                ),
            ).fetchone()
        else:
            valid = False
        if not valid:
            raise ValueError("acceptance evidence does not resolve to persisted authorized facts")


def adopt_artifacts(uow, work, evidence, event_id):
    for item in evidence:
        if item["type"] in {"artifact_version", "research_report"}:
            uow.execute(
                "INSERT INTO work_artifacts(reference_id,account_id,work_id,artifact_version_id,"
                "source_requirement_revision,role,accepted_for_revision,acceptance_event_id) "
                "VALUES (%s,%s,%s,%s,%s,'deliverable',%s,%s) ON CONFLICT DO NOTHING",
                (
                    uuid7(),
                    work["account_id"],
                    work["work_id"],
                    UUID(item["ref"]),
                    work["current_requirement_revision"],
                    work["current_requirement_revision"],
                    event_id,
                ),
            )


def supplemental_evidence(uow, work, run, criteria):
    """Resolve user and channel receipts without rewriting the producing Run."""
    evidence = list((run["result_json"] or {}).get("evidence", []))
    receipt = uow.execute(
        "SELECT d.delivery_id FROM deliveries d JOIN notifications n USING(account_id,notification_id) "
        "JOIN delivery_targets t USING(account_id,target_id) WHERE n.account_id=%s AND n.work_id=%s "
        "AND n.run_id=%s AND n.requirement_revision=%s AND n.control_epoch=%s "
        "AND n.notification_id::text=%s AND d.state='accepted' AND t.enabled "
        "AND t.target_version=d.target_version LIMIT 1",
        (
            work["account_id"],
            work["work_id"],
            run["run_id"],
            work["current_requirement_revision"],
            work["control_epoch"],
            work["continuation"].get("receipt_ref"),
        ),
    ).fetchone()
    user = uow.execute(
        "SELECT acceptance_id FROM work_result_acceptances WHERE account_id=%s AND work_id=%s "
        "AND run_id=%s AND requirement_revision=%s ORDER BY created_at DESC LIMIT 1",
        (work["account_id"], work["work_id"], run["run_id"], work["current_requirement_revision"]),
    ).fetchone()
    for criterion in criteria:
        if receipt and "delivery_receipt" in criterion["evidence_types"]:
            evidence.append(
                {
                    "criterion_id": criterion["id"],
                    "type": "delivery_receipt",
                    "ref": str(receipt["delivery_id"]),
                }
            )
        if user and "user_acceptance" in criterion["evidence_types"]:
            evidence.append(
                {
                    "criterion_id": criterion["id"],
                    "type": "user_acceptance",
                    "ref": str(user["acceptance_id"]),
                }
            )
    return evidence


def criteria_satisfied(criteria, evidence):
    return all(
        not c["required"]
        or any(e["criterion_id"] == c["id"] and e["type"] in c["evidence_types"] for e in evidence)
        for c in criteria
    )


@retryable_transaction
def integration_command(service, account, work_id, key, version, action, payload):
    operation = "accept_work_result" if action == "accept_result" else "advance_work"
    # Namespace the stable key; the existing whitelist remains a finite command contract.
    scoped_key = f"{action}:{hashlib.sha256(key.encode()).hexdigest()}"
    with UnitOfWork(service.database) as uow:
        replay, command = service._claim(
            uow,
            account,
            operation,
            scoped_key,
            {
                "action": action,
                "work_id": str(work_id),
                "row_version": version,
                "payload": dto(payload),
            },
        )
        if replay:
            return replay
        work = service._locked(uow, account, work_id, version)
        extra = {}
        if action in {"grant_resource", "input", "target", "budget"} and work["status"] in {
            "stopped",
            "completed",
        }:
            raise ValueError("terminal Work cannot acquire new resources or budget")
        if action == "budget":
            limits = _amounts(payload["limits"])
            budget = uow.execute(
                "SELECT * FROM work_budgets WHERE account_id=%s AND work_id=%s FOR UPDATE",
                (account, work_id),
            ).fetchone()
            if payload["budget_version"] != budget["version"]:
                from work_domain.commands import WorkConflict

                raise WorkConflict(work, "budget_version_conflict")
            if any(k not in budget["limits"] or v < budget["limits"][k] for k, v in limits.items()):
                raise ValueError("budget adjustment can only explicitly increase limits")
            merged = {**budget["limits"], **limits}
            uow.execute(
                "UPDATE work_budgets SET limits=%s::jsonb,version=version+1,updated_at=now() WHERE account_id=%s AND work_id=%s",
                (json.dumps(merged), account, work_id),
            )
            if work["status"] == "active" and work["continuation"]["reason"] == "budget_exhausted":
                uow.execute(
                    "UPDATE works SET continuation=%s::jsonb WHERE work_id=%s",
                    (json.dumps(continuation("ready", "budget_increased")), work_id),
                )
                WorkRepository.wakeup(uow, work, f"budget:{command}", "advance", "budget_increased")
        elif action == "grant_resource":
            from workspace.resources import ResourcePolicy

            ids = ResourcePolicy(uow).grant(
                account,
                "work",
                work_id,
                payload["node_id"],
                payload["operations"],
                payload["recursive"],
            )
            extra["grant_ids"] = ids
        elif action == "artifact_reference":
            if work["status"] in {"stopped", "completed"} or work["active_coordinator_run_id"]:
                raise ValueError("Artifact references must be selected before execution")
            if payload["role"] not in {"input", "evidence"}:
                raise ValueError("Artifact adoption requires completion evidence")
            version_id = payload["artifact_version_id"]
            if not uow.execute(
                "SELECT 1 FROM artifact_versions WHERE account_id=%s AND artifact_version_id=%s AND status='completed'",
                (account, version_id),
            ).fetchone():
                raise ResourceNotFound()
            uow.execute(
                "INSERT INTO work_artifacts(reference_id,account_id,work_id,artifact_version_id,source_requirement_revision,role) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    uuid7(),
                    account,
                    work_id,
                    version_id,
                    work["current_requirement_revision"],
                    payload["role"],
                ),
            )
        elif action == "revoke_resource":
            from workspace.resources import ResourcePolicy

            if not uow.execute(
                "SELECT 1 FROM resource_grants WHERE account_id=%s AND subject_kind='work' "
                "AND subject_id=%s AND grant_id=%s",
                (account, work_id, payload["grant_id"]),
            ).fetchone():
                raise ResourceNotFound()
            affected = ResourcePolicy(uow).revoke(account, payload["grant_id"])
            extra["affected_run_ids"] = [str(run_id) for run_id in affected]
            if work["active_coordinator_run_id"] in affected:
                service._cancel_coordinator(uow, work, command, "cancel")
        elif action == "input":
            file_id = UUID(str(payload["file_id"]))
            file = uow.execute(
                "SELECT * FROM stored_files WHERE account_id=%s AND file_id=%s AND status='ready' FOR UPDATE",
                (account, file_id),
            ).fetchone()
            if not file or file["purpose"] != "input":
                raise ResourceNotFound()
            source = payload.get("source_message_id")
            if (
                source
                and not uow.execute(
                    "SELECT 1 FROM message_files WHERE account_id=%s AND message_id=%s AND file_id=%s",
                    (account, source, file_id),
                ).fetchone()
            ):
                raise ResourceNotFound()
            uow.execute(
                "INSERT INTO work_input_refs(ref_id,account_id,work_id,file_id,source_message_id,purpose) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(account_id,work_id,file_id) DO UPDATE SET available=true,revoked_at=NULL",
                (uuid7(), account, work_id, file_id, source, payload["purpose"]),
            )
        elif action == "revoke_input":
            row = uow.execute(
                "UPDATE work_input_refs SET available=false,revoked_at=now() WHERE account_id=%s AND work_id=%s AND ref_id=%s RETURNING *",
                (account, work_id, payload["ref_id"]),
            ).fetchone()
            if not row:
                raise ResourceNotFound()
            if work["active_coordinator_run_id"]:
                service._cancel_coordinator(uow, work, command, "cancel")
        elif action == "target":
            from delivery.service import ensure_inbox, target_from_origin

            if payload.get("source_message_id"):
                source = uow.execute(
                    "SELECT origin FROM messages WHERE account_id=%s AND message_id=%s",
                    (account, payload["source_message_id"]),
                ).fetchone()
                if not source or not source["origin"]:
                    raise ResourceNotFound()
                target_from_origin(
                    uow,
                    account,
                    work_id,
                    source["origin"],
                    content_scope=payload.get("content_scope", "summary"),
                    command_id=command,
                )
            else:
                ensure_inbox(uow, account, work_id)
        elif action == "disable_target":
            target = uow.execute(
                "UPDATE delivery_targets SET enabled=false WHERE account_id=%s AND work_id=%s AND target_id=%s RETURNING target_id",
                (account, work_id, payload["target_id"]),
            ).fetchone()
            if not target:
                raise ResourceNotFound()
            uow.execute(
                "UPDATE deliveries SET state='cancelled',last_error='target_disabled',updated_at=now() WHERE account_id=%s AND target_id=%s AND state IN ('pending','failed')",
                (account, target["target_id"]),
            )
        elif action == "resolve_delivery":
            row = uow.execute(
                "SELECT d.*,n.purpose,n.requirement_revision,n.control_epoch FROM deliveries d "
                "JOIN notifications n USING(account_id,notification_id) WHERE d.account_id=%s "
                "AND n.work_id=%s AND d.delivery_id=%s AND d.state IN ('failed','uncertain') FOR UPDATE OF d",
                (account, work_id, payload["delivery_id"]),
            ).fetchone()
            if not row:
                raise ResourceNotFound()
            outcome = payload["outcome"]
            if outcome not in {"accepted", "not_sent", "retry_accepting_duplicate_risk"}:
                raise ValueError("unsupported delivery decision")
            from delivery.service import DeliveryReceiptHandler, target_authorized

            target = uow.execute(
                "SELECT * FROM delivery_targets WHERE account_id=%s AND target_id=%s",
                (account, row["target_id"]),
            ).fetchone()
            current = target_authorized(uow, target) and (
                row["purpose"] == "fact"
                or work["status"] == "active"
                and row["requirement_revision"] == work["current_requirement_revision"]
                and row["control_epoch"] == work["control_epoch"]
                and str(row["notification_id"]) == work["continuation"].get("receipt_ref")
            )
            if outcome == "retry_accepting_duplicate_risk" and not current:
                raise ValueError(
                    "obsolete fulfillment cannot be resent; resolve whether it occurred"
                )
            uow.execute(
                "INSERT INTO delivery_decisions(decision_id,account_id,delivery_id,command_id,outcome) VALUES (%s,%s,%s,%s,%s)",
                (uuid7(), account, row["delivery_id"], command, outcome),
            )
            if outcome == "accepted":
                receipt = {"level": "explicitly_confirmed", "decision_command_id": str(command)}
                delivery = uow.execute(
                    "UPDATE deliveries SET state='accepted',provider_receipt=%s::jsonb,accepted_at=now(),"
                    "lease_until=NULL,updated_at=now() WHERE delivery_id=%s RETURNING *",
                    (json.dumps(receipt), row["delivery_id"]),
                ).fetchone()
                notification = uow.execute(
                    "SELECT * FROM notifications WHERE notification_id=%s",
                    (row["notification_id"],),
                ).fetchone()
                if current:
                    DeliveryReceiptHandler.apply(uow, notification, delivery)
            else:
                uow.execute(
                    "UPDATE deliveries SET state=%s,lease_token=NULL,lease_until=NULL,available_at=now(),updated_at=now() WHERE delivery_id=%s",
                    ("pending" if current else "cancelled", row["delivery_id"]),
                )
            from run_domain.lifecycle import RunLifecycleService

            if (
                work["status"] in {"pausing", "stopping"}
                and not work["active_coordinator_run_id"]
                and not RunLifecycleService.unresolved_effect(uow, work)
            ):
                uow.execute(
                    "UPDATE works SET status=%s,control_epoch=control_epoch+1,continuation=%s::jsonb,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at),"
                    "stopped_at=CASE WHEN %s='stopped' THEN GREATEST(now(),created_at,updated_at) ELSE NULL END WHERE work_id=%s",
                    (
                        "paused" if work["status"] == "pausing" else "stopped",
                        json.dumps(continuation("blocked", "delivery_resolved")),
                        "paused" if work["status"] == "pausing" else "stopped",
                        work_id,
                    ),
                )
                WorkRepository.event(
                    uow,
                    WorkRepository.get(uow, account, work_id),
                    "paused" if work["status"] == "pausing" else "stopped",
                    command_id=command,
                )
        elif action == "accept_result":
            if (
                payload["requirement_revision"] != work["current_requirement_revision"]
                or work["status"] != "active"
                or work["active_coordinator_run_id"]
            ):
                raise ValueError("acceptance revision or control mismatch")
            requirement = WorkRepository.requirement(
                uow, account, work_id, work["current_requirement_revision"]
            )
            run = uow.execute(
                "SELECT r.* FROM runs r JOIN artifact_versions v ON v.account_id=r.account_id "
                "WHERE r.account_id=%s AND r.work_id=%s AND r.status='succeeded' AND r.requirement_revision=%s "
                "AND r.work_control_epoch=%s AND v.artifact_version_id=%s AND v.status='completed' "
                "AND EXISTS(SELECT 1 FROM jsonb_array_elements(r.result_json->'evidence') e WHERE e->>'ref'=v.artifact_version_id::text "
                "AND e->>'type' IN ('artifact_version','research_report')) ORDER BY r.created_at DESC LIMIT 1",
                (
                    account,
                    work_id,
                    work["current_requirement_revision"],
                    work["control_epoch"],
                    payload["artifact_version_id"],
                ),
            ).fetchone()
            if not run or not any(
                "user_acceptance" in c["evidence_types"] for c in requirement["acceptance_criteria"]
            ):
                raise ValueError("result is not awaiting explicit user acceptance")
            acceptance = uuid7()
            uow.execute(
                "INSERT INTO work_result_acceptances(acceptance_id,account_id,work_id,requirement_revision,run_id,artifact_version_id,command_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    acceptance,
                    account,
                    work_id,
                    work["current_requirement_revision"],
                    run["run_id"],
                    payload["artifact_version_id"],
                    command,
                ),
            )
            evidence = supplemental_evidence(uow, work, run, requirement["acceptance_criteria"])
            from work_domain.completion import WorkCompletionPolicy

            if criteria_satisfied(requirement["acceptance_criteria"], evidence):
                WorkCompletionPolicy.accept(uow, work, dict(run), user_evidence=evidence)
            else:
                uow.execute(
                    "UPDATE works SET row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s",
                    (work_id,),
                )
        else:
            raise ValueError("unsupported integration command")
        if action != "accept_result" and WorkRepository.get(uow, account, work_id)[
            "status"
        ] not in {"stopped", "completed"}:
            uow.execute(
                "UPDATE works SET row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s",
                (work_id,),
            )
        current = WorkRepository.get(uow, account, work_id)
        WorkRepository.event(uow, current, action, command_id=command, **dto(payload))
        return service._complete(
            uow,
            account,
            operation,
            scoped_key,
            work_id,
            201 if action == "grant_resource" else 200,
            **extra,
        )
