"""Transactional Task commands for Research Runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from uuid6 import uuid7

from persistence.repositories import (
    AccountRepository,
    ConversationRepository,
    IdempotencyRepository,
    MessageRepository,
    OutboxRepository,
    RunBudgetRepository,
)
from persistence.uow import UnitOfWork, retryable_transaction
from research_domain.models import SourceStrategy
from research_domain.persistence import TaskRepository
from web_domain.errors import DomainError, IdempotencyConflict, ResourceNotFound
from workspace.resources import ResourcePolicy


class TaskNotActive(DomainError):
    pass


class TaskBusy(DomainError):
    pass


@dataclass(frozen=True)
class TaskCommandResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool = False


def _digest(value: object) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).digest()


class ResearchTaskCommandService:
    """Create/trigger Task commands using the existing PostgreSQL UoW and Outbox."""

    def __init__(self, database: object, *, budget_mode: str = "enforce") -> None:
        self.database = database
        self.accounts = AccountRepository()
        self.conversations = ConversationRepository()
        self.messages = MessageRepository()
        self.tasks = TaskRepository()
        self.idempotency = IdempotencyRepository()
        self.outbox = OutboxRepository()
        self.budgets = RunBudgetRepository()
        self.budget_mode = budget_mode

    def list_tasks(self, account_id: UUID, before: UUID | None = None) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            rows = uow.execute(
                "SELECT t.task_id,t.title,t.status,t.created_at,t.output_directory_id,"
                "t.output_required,t.output_operation,t.output_entry_id,"
                "t.schedule_type,t.schedule_timezone,t.schedule_expression,"
                "n.name AS output_directory_name "
                "FROM tasks t LEFT JOIN workspace_nodes n ON n.node_id=t.output_directory_id "
                "WHERE t.account_id=%s "
                "AND (%s::uuid IS NULL OR t.task_id<%s::uuid) "
                "ORDER BY t.task_id DESC LIMIT 51",
                (account_id, before, before),
            ).fetchall()
            page = rows[:50]
            return {"items": [
                {"task_id": str(row["task_id"]), "title": row["title"],
                 "status": row["status"], "created_at": row["created_at"].isoformat(),
                 "output_directory_id": str(row["output_directory_id"]) if row["output_directory_id"] else None,
                 "output_directory_name": row["output_directory_name"],
                 "output_required": row["output_required"],
                 "output_operation": row["output_operation"],
                 "output_entry_id": str(row["output_entry_id"]) if row["output_entry_id"] else None,
                 "schedule_type": row["schedule_type"],
                 "schedule_timezone": row["schedule_timezone"],
                 "schedule_expression": row["schedule_expression"]}
                for row in page
            ], "next_before": str(page[-1]["task_id"]) if len(rows) > 50 else None}

    def list_runs(
        self, account_id: UUID, task_id: UUID, before: UUID | None = None,
    ) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            if uow.execute(
                "SELECT 1 FROM tasks WHERE account_id=%s AND task_id=%s",
                (account_id, task_id),
            ).fetchone() is None:
                raise ResourceNotFound()
            rows = uow.execute(
                "SELECT r.run_id,r.status,r.failure_code,r.created_at,sf.file_id,sf.display_name,"
                "i.state AS save_state,i.entry_id,i.failure_code AS save_failure_code "
                "FROM runs r LEFT JOIN LATERAL ("
                "SELECT sf.file_id,sf.display_name FROM run_files rf "
                "JOIN stored_files sf ON sf.account_id=rf.account_id "
                "AND sf.file_id=rf.file_id AND sf.status='ready' "
                "WHERE rf.account_id=r.account_id AND rf.run_id=r.run_id "
                "AND rf.direction='output' ORDER BY rf.created_at LIMIT 1"
                ") sf ON true LEFT JOIN research_run_save_intents i ON i.run_id=r.run_id "
                "WHERE r.account_id=%s AND r.task_id=%s "
                "AND (%s::uuid IS NULL OR r.run_id<%s::uuid) "
                "ORDER BY r.run_id DESC LIMIT 51",
                (account_id, task_id, before, before),
            ).fetchall()
            page = rows[:50]
            return {"items": [{
                "run_id": str(row["run_id"]), "status": row["status"],
                "failure_code": row["failure_code"],
                "save_status": row["save_state"],
                "save_entry_id": str(row["entry_id"]) if row["entry_id"] else None,
                "save_failure_code": row["save_failure_code"],
                "created_at": row["created_at"].isoformat(),
                "published_file": ({"file_id": str(row["file_id"]),
                    "file_name": row["display_name"],
                    "download_url": f"/api/v1/files/{row['file_id']}/content"}
                    if row["file_id"] else None),
            } for row in page],
                "next_before": str(page[-1]["run_id"]) if len(rows) > 50 else None}

    @retryable_transaction
    def create_task(
        self,
        account_id: UUID,
        key: str,
        title: str,
        objective: str,
        source_strategy: SourceStrategy | None = None,
        *,
        conversation_id: UUID | None = None,
        output_directory_id: UUID | None = None,
        output_required: bool = False,
    ) -> TaskCommandResult:
        title = title.strip()
        objective = objective.strip()
        if not title or not objective:
            raise ValueError("title and objective are required")
        strategy = source_strategy or SourceStrategy()
        payload = {"title": title, "objective": objective, "source_strategy": strategy.to_dict()}
        if conversation_id is not None:
            payload["conversation_id"] = str(conversation_id)
        payload.update(output_directory_id=str(output_directory_id) if output_directory_id else None,
                       output_required=output_required)
        with UnitOfWork(self.database) as uow:
            replay = self._claim(uow, account_id, "create_task", key, payload)
            if replay:
                return replay
            if not self.accounts.require_active(uow, account_id):
                raise ResourceNotFound()
            if conversation_id is not None and not self.conversations.lock_active(
                uow, account_id, conversation_id
            ):
                raise ResourceNotFound()
            task_id = uuid7()
            self.tasks.insert(
                uow, task_id, account_id, title, objective, strategy.to_dict(), conversation_id
            )
            if output_directory_id is not None:
                directory = uow.execute(
                    "SELECT 1 FROM workspace_nodes WHERE account_id=%s AND node_id=%s "
                    "AND kind='directory' AND deleted_at IS NULL",
                    (account_id, output_directory_id),
                ).fetchone()
                if directory is None:
                    raise ResourceNotFound()
                uow.execute(
                    "UPDATE tasks SET output_directory_id=%s,output_required=%s WHERE task_id=%s",
                    (output_directory_id, output_required, task_id),
                )
                ResourcePolicy._version(uow, account_id, "task", task_id)
                for operation in ("list_metadata", "read_content", "create_child"):
                    uow.execute(
                        "INSERT INTO resource_grants(grant_id,account_id,subject_kind,"
                        "subject_id,node_id,operation,recursive) "
                        "VALUES (%s,%s,'task',%s,%s,%s,true)",
                        (uuid7(), account_id, task_id, output_directory_id, operation),
                    )
            elif output_required:
                raise ValueError("required output needs a directory")
            body = {"task_id": str(task_id), "task_type": "research_report", "status": "active"}
            if conversation_id is not None:
                body["conversation_id"] = str(conversation_id)
            self._complete(uow, account_id, "create_task", key, 201, body)
            return TaskCommandResult(201, body)

    @retryable_transaction
    def trigger_task(self, account_id: UUID, task_id: UUID, key: str) -> TaskCommandResult:
        payload = {"task_id": str(task_id)}
        with UnitOfWork(self.database) as uow:
            replay = self._claim(uow, account_id, "trigger_task", key, payload)
            if replay:
                return replay
            task = self.tasks.lock_active(uow, account_id, task_id)
            if task is None:
                raise ResourceNotFound()
            if task["status"] != "active":
                raise TaskNotActive(str(task["status"]))
            active = uow.execute(
                "SELECT run_id FROM runs WHERE task_id=%s AND status IN "
                "('queued','running','cancelling')",
                (task_id,),
            ).fetchone()
            if active is not None:
                raise TaskBusy(str(active["run_id"]))
            run_id = uuid7()
            workflow_id = f"hpagent-research-{run_id}"
            conversation_id = task["conversation_id"]
            if conversation_id is None:
                uow.execute(
                    "INSERT INTO runs(run_id,account_id,task_id,run_kind,workflow_id,agent_strategy) "
                    "VALUES (%s,%s,%s,'research',%s,NULL)",
                    (run_id, account_id, task_id, workflow_id),
                )
            else:
                conversation = self.conversations.lock_active(
                    uow, account_id, conversation_id
                )
                if conversation is None:
                    raise ResourceNotFound()
                active = uow.execute(
                    "SELECT 1 FROM runs WHERE conversation_id=%s AND status IN "
                    "('queued','running','cancelling')", (conversation_id,),
                ).fetchone()
                if active is not None:
                    raise TaskBusy("conversation has an active Run")
                allocated = self.conversations.allocate_messages(
                    uow, account_id, conversation_id, 2
                )
                user_message_id, assistant_message_id = uuid7(), uuid7()
                self.messages.insert_user(
                    uow, user_message_id, account_id, conversation_id,
                    str(task["objective"]), allocated - 1, uuid7(),
                )
                uow.execute(
                    "INSERT INTO runs(run_id,account_id,conversation_id,task_id,run_kind,"
                    "trigger_message_id,workflow_id,context_message_seq,agent_strategy) "
                    "VALUES (%s,%s,%s,%s,'research',%s,%s,%s,NULL)",
                    (run_id, account_id, conversation_id, task_id, user_message_id,
                     workflow_id, allocated - 1),
                )
                self.messages.insert_assistant(
                    uow, assistant_message_id, account_id, conversation_id, allocated, run_id
                )
            limits = {
                "sources_discovered": 30,
                "source_fetches": 20,
                "research_iterations": 3,
                "model_input_tokens": 80_000,
                "model_output_tokens": 20_000,
                "model_total_tokens": 100_000,
                "model_calls": 20,
                "wall_time_ms": 1_800_000,
            }
            self.budgets.create_snapshot(
                uow,
                run_id,
                account_id,
                conversation_id,
                "research-r0-r4-v1",
                self.budget_mode,
                json.dumps(limits, sort_keys=True),
                0,
            )
            ResourcePolicy(self.database).snapshot_in_uow(
                uow, account_id, run_id, task_id, "task"
            )
            if task["output_directory_id"] is not None:
                target_entry = task["output_entry_id"]
                expected_revision = None
                expected_sha256 = None
                if task["output_operation"] == "update_content":
                    base = uow.execute(
                        "SELECT d.current_revision,d.current_file_id,d.current_sha256 "
                        "FROM workspace_nodes n JOIN persistent_file_destinations d "
                        "ON d.destination_id=n.destination_id AND d.account_id=n.account_id "
                        "WHERE n.account_id=%s AND n.node_id=%s AND n.parent_id=%s "
                        "AND n.deleted_at IS NULL FOR SHARE OF d",
                        (account_id, target_entry, task["output_directory_id"]),
                    ).fetchone()
                    if base is None:
                        raise ResourceNotFound()
                    grants = ResourcePolicy._grants(uow, account_id, "task", task_id,
                                                     target_entry, "read_content")
                    if not grants:
                        raise ResourceNotFound()
                    fixed = uow.execute(
                        "UPDATE run_resource_candidates SET fixed_file_id=%s,"
                        "fixed_revision=%s,fixed_at=now() WHERE run_id=%s AND node_id=%s "
                        "RETURNING logical_name",
                        (base["current_file_id"], base["current_revision"], run_id,
                         target_entry),
                    ).fetchone()
                    if fixed is None:
                        raise ResourceNotFound()
                    uow.execute(
                        "INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,"
                        "node_id,basis,grant_id) VALUES (%s,%s,%s,%s,%s,'workspace_grant',%s)",
                        (uuid7(), account_id, run_id, base["current_file_id"], target_entry,
                         grants[0]["grant_id"]),
                    )
                    uow.execute(
                        "INSERT INTO run_files(account_id,conversation_id,run_id,file_id,"
                        "direction,logical_name) VALUES (%s,%s,%s,%s,'input',%s)",
                        (account_id, conversation_id, run_id, base["current_file_id"],
                         fixed["logical_name"]),
                    )
                    expected_revision = base["current_revision"]
                    expected_sha256 = base["current_sha256"]
                uow.execute(
                    "INSERT INTO research_run_save_intents(run_id,account_id,target_directory_id,"
                    "operation,output_kind,policy_version,required,operation_id,"
                    "target_entry_id,expected_revision,expected_sha256) "
                    "VALUES (%s,%s,%s,%s,'research_markdown',%s,%s,%s,%s,%s,%s)",
                    (run_id, account_id, task["output_directory_id"],
                     task["output_operation"], task["output_policy_version"],
                     task["output_required"], f"workspace-save:{run_id}:research_markdown",
                     target_entry, expected_revision, expected_sha256),
                )
            self.outbox.enqueue(
                uow,
                uuid7(),
                account_id,
                "start_research_run",
                f"start-research-run:{run_id}",
                conversation_id,
                run_id,
                json.dumps({"run_id": str(run_id), "task_id": str(task_id), "version": 1}),
            )
            self.tasks.mark_triggered(uow, task_id)
            body = {
                "task_id": str(task_id),
                "run_id": str(run_id),
                "run_kind": "research",
                "workflow_id": workflow_id,
                "status": "queued",
            }
            if conversation_id is not None:
                body["conversation_id"] = str(conversation_id)
            self._complete(uow, account_id, "trigger_task", key, 202, body)
            return TaskCommandResult(202, body)

    @retryable_transaction
    def update_output(self, account_id: UUID, task_id: UUID, directory_id: UUID,
                      required: bool, operation: str = "create_child",
                      entry_id: UUID | None = None) -> dict[str, Any]:
        if operation not in {"create_child", "update_content"} or (
            (operation == "create_child") != (entry_id is None)
        ):
            raise ValueError("invalid Task output operation")
        with UnitOfWork(self.database) as uow:
            task = self.tasks.lock_active(uow, account_id, task_id)
            if task is None:
                raise ResourceNotFound()
            if uow.execute(
                "SELECT 1 FROM workspace_nodes WHERE account_id=%s AND node_id=%s "
                "AND kind='directory' AND deleted_at IS NULL",
                (account_id, directory_id),
            ).fetchone() is None:
                raise ResourceNotFound()
            if entry_id is not None:
                if uow.execute(
                    "SELECT 1 FROM workspace_nodes n JOIN persistent_file_destinations d "
                    "ON d.destination_id=n.destination_id AND d.account_id=n.account_id "
                    "WHERE n.account_id=%s AND n.node_id=%s AND n.parent_id=%s "
                    "AND n.kind='file' AND n.deleted_at IS NULL",
                    (account_id, entry_id, directory_id),
                ).fetchone() is None:
                    raise ResourceNotFound()
            ResourcePolicy._version(uow, account_id, "task", task_id)
            for permission in ("list_metadata", "read_content",
                               "create_child" if operation == "create_child" else "update_content"):
                if not ResourcePolicy._grants(uow, account_id, "task", task_id,
                                              entry_id or directory_id, permission):
                    uow.execute(
                        "INSERT INTO resource_grants(grant_id,account_id,subject_kind,"
                        "subject_id,node_id,operation,recursive) "
                        "VALUES (%s,%s,'task',%s,%s,%s,true)",
                        (uuid7(), account_id, task_id, entry_id or directory_id, permission),
                    )
            uow.execute(
                "UPDATE resource_policy_versions SET version=version+1 WHERE account_id=%s "
                "AND subject_kind='task' AND subject_id=%s", (account_id, task_id),
            )
            uow.execute(
                "UPDATE tasks SET output_directory_id=%s,output_required=%s,"
                "output_operation=%s,output_entry_id=%s,"
                "output_policy_version=output_policy_version+1,"
                "updated_at=now(),version=version+1 WHERE task_id=%s",
                (directory_id, required, operation, entry_id, task_id),
            )
            return {"task_id": str(task_id), "output_directory_id": str(directory_id),
                    "output_required": required, "operation": operation,
                    "output_entry_id": str(entry_id) if entry_id else None}

    @retryable_transaction
    def update_schedule(
        self,
        account_id: UUID,
        task_id: UUID,
        key: str,
        *,
        schedule_type: str,
        timezone: str,
        expression: str | None,
        enabled: bool,
    ) -> TaskCommandResult:
        if schedule_type not in {"manual", "daily"}:
            raise ValueError("schedule_type must be manual or daily")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown schedule timezone") from exc
        if schedule_type == "manual":
            enabled, expression = False, None
        elif expression is None or not __import__("re").fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", expression
        ):
            raise ValueError("daily schedule expression must be HH:MM")
        payload = {
            "task_id": str(task_id), "schedule_type": schedule_type,
            "timezone": timezone, "expression": expression, "enabled": enabled,
        }
        with UnitOfWork(self.database) as uow:
            replay = self._claim(uow, account_id, "update_task_schedule", key, payload)
            if replay:
                return replay
            task = self.tasks.lock_active(uow, account_id, task_id)
            if task is None:
                raise ResourceNotFound()
            updated = self.tasks.update_schedule(
                uow, task_id, schedule_type=schedule_type, timezone=timezone,
                expression=expression, enabled=enabled,
            )
            body = {
                "task_id": str(task_id), "schedule_type": updated["schedule_type"],
                "timezone": updated["schedule_timezone"],
                "expression": updated["schedule_expression"],
                "enabled": bool(updated["schedule_enabled"]),
                "schedule_version": int(updated["schedule_version"]),
            }
            self._complete(uow, account_id, "update_task_schedule", key, 200, body)
            return TaskCommandResult(200, body)

    @retryable_transaction
    def get_run(self, account_id: UUID, task_id: UUID, run_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT r.run_id,r.task_id,r.status,r.failure_code,r.failure_message,"
                "r.created_at,r.started_at,r.finished_at,rr.report_markdown,"
                "rr.report_structured_json,rr.citation_status,rr.artifact_id,"
                "rr.artifact_version_id,i.state AS save_status,i.entry_id AS save_entry_id,"
                "i.failure_code AS save_failure_code FROM runs r LEFT JOIN research_reports rr "
                "ON rr.run_id=r.run_id LEFT JOIN research_run_save_intents i "
                "ON i.run_id=r.run_id WHERE r.account_id=%s AND r.task_id=%s "
                "AND r.run_id=%s AND r.run_kind='research'",
                (account_id, task_id, run_id),
            ).fetchone()
            if row is None:
                raise ResourceNotFound()
            result = dict(row)
            output = uow.execute(
                "SELECT sf.file_id,sf.display_name FROM run_files rf JOIN stored_files sf "
                "ON sf.account_id=rf.account_id AND sf.file_id=rf.file_id "
                "WHERE rf.account_id=%s AND rf.run_id=%s AND rf.direction='output' "
                "AND sf.status='ready' ORDER BY rf.created_at LIMIT 1",
                (account_id, run_id),
            ).fetchone()
            result["published_file"] = (
                {"file_id": str(output["file_id"]), "file_name": output["display_name"],
                 "download_url": f"/api/v1/files/{output['file_id']}/content"}
                if output else None
            )
            for key in ("run_id", "task_id", "artifact_id", "artifact_version_id", "save_entry_id"):
                result[key] = str(result[key]) if result[key] is not None else None
            for key in ("created_at", "started_at", "finished_at"):
                result[key] = result[key].isoformat() if result[key] is not None else None
            return result

    @retryable_transaction
    def list_evidence(
        self, account_id: UUID, task_id: UUID, run_id: UUID
    ) -> list[dict[str, Any]]:
        with UnitOfWork(self.database) as uow:
            rows = uow.execute(
                "SELECT e.evidence_id,e.source_id,e.excerpt,e.content_ref,e.source_locator,"
                "e.source_quality,s.canonical_uri,s.title FROM evidence_items e "
                "JOIN source_records s ON s.source_id=e.source_id "
                "JOIN runs r ON r.run_id=e.run_id WHERE r.account_id=%s AND r.task_id=%s "
                "AND r.run_id=%s AND e.run_id=r.run_id AND s.run_id=r.run_id "
                "ORDER BY e.created_at,e.evidence_id",
                (account_id, task_id, run_id),
            ).fetchall()
            if not rows:
                exists = uow.execute(
                    "SELECT 1 FROM runs WHERE account_id=%s AND task_id=%s AND run_id=%s "
                    "AND run_kind='research'", (account_id, task_id, run_id),
                ).fetchone()
                if exists is None:
                    raise ResourceNotFound()
            return [
                {**dict(row), "evidence_id": str(row["evidence_id"]),
                 "source_id": str(row["source_id"])} for row in rows
            ]

    @retryable_transaction
    def get_report(self, account_id: UUID, task_id: UUID, run_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT rr.* FROM research_reports rr JOIN runs r ON r.run_id=rr.run_id "
                "WHERE r.account_id=%s AND r.task_id=%s AND r.run_id=%s",
                (account_id, task_id, run_id),
            ).fetchone()
            if row is None:
                raise ResourceNotFound()
            result = dict(row)
            for key_name in ("run_id", "previous_run_id", "artifact_id", "artifact_version_id"):
                result[key_name] = (
                    str(result[key_name]) if result[key_name] is not None else None
                )
            return result

    def _claim(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation: str,
        key: str,
        payload: object,
    ) -> TaskCommandResult | None:
        digest = _digest(payload)
        row = self.idempotency.claim(
            uow,
            uuid7(),
            account_id,
            operation,
            key,
            digest,
            datetime.now(UTC) + timedelta(days=7),
        )
        if row is None:
            return None
        if bytes(row["request_hash"]) != digest:
            raise IdempotencyConflict()
        if row["status"] != "completed":
            raise TaskBusy("command is in progress")
        return TaskCommandResult(
            int(row["response_status"]), dict(row["response_body"]), replayed=True
        )

    def _complete(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation: str,
        key: str,
        status: int,
        body: dict[str, Any],
    ) -> None:
        self.idempotency.complete(
            uow, account_id, operation, key, status, json.dumps(body, sort_keys=True)
        )
