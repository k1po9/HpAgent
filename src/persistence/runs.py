"""Ownership-scoped repositories.  No method accepts a bare public resource ID."""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

from run_domain.creation import ChatRunInsert, WorkRunInsert, build_chat_run_insert

from .uow import UnitOfWork


class RunRepository:
    def get_for_account(
        self, uow: UnitOfWork, account_id: UUID, run_id: UUID, *, lock: bool = False
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if lock else ""
        return cast(
            dict[str, Any] | None,
            uow.execute(
                "SELECT * FROM runs WHERE account_id=%s AND run_id=%s" + suffix,
                (account_id, run_id),
            ).fetchone(),
        )

    def context_subject(
        self, uow: UnitOfWork, account_id: UUID, run_id: UUID
    ) -> dict[str, Any] | None:
        """Load the owned chat Run/Execution/Conversation chain for Context assembly."""
        return cast(
            dict[str, Any] | None,
            uow.execute(
                "SELECT r.run_id,r.account_id,r.conversation_id,r.session_id,"
                "r.trigger_message_id,r.context_message_seq,r.agent_strategy,r.status AS run_status,"
                "e.execution_id,"
                "t.role AS trigger_role,t.status AS trigger_status,t.content AS trigger_content,t.origin,"
                "t.created_at AS trigger_created_at "
                "FROM runs r JOIN conversations c ON c.account_id=r.account_id "
                "AND c.conversation_id=r.conversation_id JOIN run_executions e "
                "ON e.account_id=r.account_id AND e.run_id=r.run_id AND e.role='root' "
                "JOIN messages t ON t.account_id=r.account_id "
                "AND t.conversation_id=r.conversation_id AND t.message_id=r.trigger_message_id "
                "WHERE r.account_id=%s AND r.run_id=%s",
                (account_id, run_id),
            ).fetchone(),
        )

    def discover_conversation(self, uow: UnitOfWork, account_id: UUID, run_id: UUID) -> UUID | None:
        row = uow.execute(
            "SELECT conversation_id FROM runs WHERE account_id=%s AND run_id=%s",
            (account_id, run_id),
        ).fetchone()
        return cast(UUID | None, row["conversation_id"] if row else None)

    def has_active(self, uow: UnitOfWork, conversation_id: UUID) -> bool:
        return (
            uow.execute(
                "SELECT 1 FROM runs WHERE conversation_id=%s "
                "AND status IN ('queued','running','cancelling')",
                (conversation_id,),
            ).fetchone()
            is not None
        )

    def direct_retry(self, uow: UnitOfWork, source_run_id: UUID):
        return uow.execute(
            "SELECT r.run_id,r.status,r.retry_of_run_id,m.message_id AS assistant_message_id,"
            "m.sequence AS assistant_sequence,m.status AS assistant_status "
            "FROM runs r JOIN messages m ON m.produced_by_run_id=r.run_id "
            "WHERE r.retry_of_run_id=%s",
            (source_run_id,),
        ).fetchone()

    def insert(
        self,
        uow: UnitOfWork,
        run_id: UUID,
        account_id: UUID,
        conversation_id: UUID,
        session_id: UUID | None,
        trigger_message_id: UUID,
        workflow_id: str,
        context_message_seq: int,
        retry_of_run_id: UUID | None = None,
        agent_strategy: str = "react",
        fixed_html: dict[str, Any] | None = None,
        agent_policy: dict[str, Any] | None = None,
    ) -> None:
        # Compatibility only; primary callers freeze the record in application.
        self.insert_chat(
            uow,
            build_chat_run_insert(
                run_id,
                account_id,
                conversation_id,
                session_id,
                trigger_message_id,
                workflow_id,
                context_message_seq,
                retry_of_run_id,
                agent_strategy,
                fixed_html,
                agent_policy,
            ),
        )

    def set_cancelling(self, uow: UnitOfWork, run_id: UUID) -> None:
        uow.execute(
            "UPDATE runs SET status='cancelling',version=version+1,updated_at=now() "
            "WHERE run_id=%s AND status IN ('queued','running')",
            (run_id,),
        )

    def set_terminal(
        self,
        uow: UnitOfWork,
        run_id: UUID,
        status: str,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        uow.execute(
            "UPDATE runs SET status=%s,failure_code=%s,failure_message=%s,"
            "finished_at=GREATEST(now(),created_at,COALESCE(started_at,created_at)),"
            "version=version+1,updated_at=now() WHERE run_id=%s",
            (status, failure_code, failure_message, run_id),
        )

    def insert_chat(self, uow: UnitOfWork, record: ChatRunInsert) -> None:
        uow.execute(
            "INSERT INTO runs(run_id,account_id,conversation_id,session_id,trigger_message_id,"
            "retry_of_run_id,workflow_id,context_message_seq,agent_strategy,strategy_kind,executor_key,"
            "executor_version,strategy_policy_version,input_snapshot) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (
                record.run_id,
                record.account_id,
                record.conversation_id,
                record.session_id,
                record.trigger_message_id,
                record.retry_of_run_id,
                record.workflow_id,
                record.context_message_seq,
                record.agent_strategy,
                record.strategy_kind,
                record.executor_key,
                record.executor_version,
                record.strategy_policy_version,
                json.dumps(record.input_snapshot),
            ),
        )

    def insert_work(self, uow: UnitOfWork, record: WorkRunInsert) -> None:
        uow.execute(
            "INSERT INTO runs(run_id,account_id,source_kind,work_id,requirement_revision,"
            "work_control_epoch,wakeup_id,strategy_kind,executor_key,workflow_id,agent_strategy,retry_of_run_id,"
            "executor_version,strategy_policy_version,input_snapshot) "
            "VALUES (%s,%s,'work',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (
                record.run_id,
                record.account_id,
                record.work_id,
                record.requirement_revision,
                record.work_control_epoch,
                record.wakeup_id,
                record.strategy_kind,
                record.executor_key,
                record.workflow_id,
                record.agent_strategy,
                record.retry_of_run_id,
                record.executor_version,
                record.strategy_policy_version,
                json.dumps(record.input_snapshot),
            ),
        )

    def get_in_uow(self, uow: UnitOfWork, run_id: UUID):
        """Internal projection after the caller has established Run authority."""
        return uow.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone()

    def root_execution_id(self, uow: UnitOfWork, run_id: UUID):
        return uow.execute(
            "SELECT execution_id FROM run_executions WHERE run_id=%s AND role='root'", (run_id,)
        ).fetchone()["execution_id"]

    def belongs_to_conversation(self, uow, account_id, run_id, conversation_id):
        return uow.execute(
            "SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s AND conversation_id=%s",
            (account_id, run_id, conversation_id),
        ).fetchone()

    def find_surface_run(self, uow, account_id, cid, target_run=None):
        return uow.execute(
            "SELECT run_id FROM runs WHERE account_id=%s AND conversation_id=%s "
            + (
                "AND run_id=%s" if target_run else "AND status IN ('queued','running','cancelling')"
            ),
            (account_id, cid, target_run) if target_run else (account_id, cid),
        ).fetchone()
