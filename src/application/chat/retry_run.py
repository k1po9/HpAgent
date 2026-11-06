"""Retry a failed Chat Run, preserving source locks and direct-child reuse."""

from __future__ import annotations

import logging

from uuid6 import uuid7

from application.chat.dependencies import ChatCommandDependencies
from application.chat.projection import ChatProjection
from common.logging import log_event
from persistence.command_result import CommandResult
from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.creation import build_chat_run_insert
from web_domain.errors import (
    FileNotReady,
    ResourceNotFound,
    RunNotRetryable,
    RunRetryNotSafe,
)
from web_domain.failures import is_failure_retryable

from .commands import RetryChatRunCommand

logger = logging.getLogger("HpAgent.ConversationCommands")


class RetryChatRun:
    def __init__(self, dependencies: ChatCommandDependencies):
        self.dependencies = dependencies

    @retryable_transaction
    def execute(self, command: RetryChatRunCommand) -> CommandResult:
        account_id, source_run_id, key = command.account_id, command.source_run_id, command.key
        payload = {"run_id": str(source_run_id)}
        with UnitOfWork(self.dependencies.database) as uow:
            existing = self.dependencies.claim(uow, account_id, "retry_run", key, payload)
            if existing:
                return existing
            conversation_id = self.dependencies.runs.discover_conversation(
                uow, account_id, source_run_id
            )
            if conversation_id is None:
                source = self.dependencies.runs.get_for_account(uow, account_id, source_run_id)
                if source and source["source_kind"] == "work":
                    from work_domain.errors import WorkConflict

                    raise WorkConflict({"work_id": source["work_id"]}, "use_work_advance")
                raise ResourceNotFound()
            conversation = self.dependencies.conversations.get_for_account(
                uow, account_id, conversation_id, lock=True
            )
            source = self.dependencies.runs.get_for_account(
                uow, account_id, source_run_id, lock=True
            )
            if source is None:
                raise ResourceNotFound()
            if source["status"] != "failed":
                raise RunNotRetryable()
            failure_code = str(source.get("failure_code") or "")
            if not is_failure_retryable(failure_code):
                log_event(
                    logger,
                    logging.WARNING,
                    "run_retry_rejected",
                    "run",
                    run_id=str(source_run_id),
                    account_id=str(account_id),
                    failure_code=failure_code,
                    reason=RunRetryNotSafe(failure_code).reason,
                    status="rejected",
                )
                raise RunRetryNotSafe(failure_code)
            child = self.dependencies.runs.direct_retry(uow, source_run_id)
            if child:
                result = ChatProjection.retry_result(uow, source_run_id, child["run_id"])
                response_status = 200
                resource_reused = True
            else:
                if conversation is None or conversation["status"] != "active":
                    raise ResourceNotFound()
                self.dependencies.admission_policy.admit_in_locked_conversation(
                    uow, source["conversation_id"]
                )
                session_id = None
                sequence = self.dependencies.conversations.allocate_messages(
                    uow, account_id, source["conversation_id"], 1
                )
                run_id = uuid7()
                assistant_message_id = uuid7()
                self.dependencies.runs.insert_chat(
                    uow,
                    build_chat_run_insert(
                        run_id,
                        account_id,
                        source["conversation_id"],
                        session_id,
                        source["trigger_message_id"],
                        f"hpagent-web-run-{run_id}",
                        source["context_message_seq"],
                        source_run_id,
                        str(source.get("agent_strategy") or "react"),
                        fixed_html=source["input_snapshot"]
                        if source["executor_key"] == "artifact_html"
                        else None,
                        agent_policy=self.dependencies.agent_policy.to_snapshot(),
                    ),
                )
                self.dependencies.messages.insert_assistant(
                    uow,
                    assistant_message_id,
                    account_id,
                    source["conversation_id"],
                    sequence,
                    run_id,
                )
                frozen_ids = [
                    row["file_id"]
                    for row in uow.execute(
                        "SELECT file_id FROM run_files WHERE run_id=%s AND direction='input'",
                        (source_run_id,),
                    ).fetchall()
                ]
                ready_count = uow.execute(
                    "SELECT count(*) AS count FROM stored_files WHERE account_id=%s "
                    "AND file_id=ANY(%s) AND status='ready'",
                    (account_id, frozen_ids),
                ).fetchone()["count"]
                if ready_count != len(frozen_ids):
                    raise FileNotReady()
                self.dependencies.files.copy_retry_inputs(uow, source_run_id, run_id)
                self.dependencies.resource_policy.copy_retry_in_uow(uow, source_run_id, run_id)
                self.dependencies.create_budget(uow, run_id, account_id, source["conversation_id"])
                self.dependencies.enqueue_run_event(
                    uow, account_id, source["conversation_id"], run_id, "start_run"
                )
                result = ChatProjection.retry_result(uow, source_run_id, run_id)
                response_status = 202
                resource_reused = False
            self.dependencies.complete(uow, account_id, "retry_run", key, response_status, result)
            return CommandResult(response_status, result, resource_reused=resource_reused)
