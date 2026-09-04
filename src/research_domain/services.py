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
    ) -> TaskCommandResult:
        title = title.strip()
        objective = objective.strip()
        if not title or not objective:
            raise ValueError("title and objective are required")
        strategy = source_strategy or SourceStrategy()
        payload = {"title": title, "objective": objective, "source_strategy": strategy.to_dict()}
        if conversation_id is not None:
            payload["conversation_id"] = str(conversation_id)
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
                "rr.artifact_version_id FROM runs r LEFT JOIN research_reports rr "
                "ON rr.run_id=r.run_id WHERE r.account_id=%s AND r.task_id=%s "
                "AND r.run_id=%s AND r.run_kind='research'",
                (account_id, task_id, run_id),
            ).fetchone()
            if row is None:
                raise ResourceNotFound()
            result = dict(row)
            for key in ("run_id", "task_id", "artifact_id", "artifact_version_id"):
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
