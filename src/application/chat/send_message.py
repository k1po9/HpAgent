"""Send one Chat message within an owned or caller-provided transaction."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from uuid6 import uuid7

from common.logging import log_event
from persistence.command_result import CommandResult
from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.creation import build_chat_run_insert
from web_domain.errors import (
    FileAlreadyBound,
    FileNotReady,
    IdempotencyConflict,
    ResourceNotFound,
)
from workspace.resources import ResourceDenied

from .commands import SendMessageCommand, message_request_id
from .dependencies import ChatCommandDependencies
from .projection import ChatProjection

logger = logging.getLogger("HpAgent.ConversationCommands")


class SendMessage:
    def __init__(self, dependencies: ChatCommandDependencies):
        self.dependencies = dependencies

    @retryable_transaction
    def execute(self, command: SendMessageCommand) -> CommandResult:
        with UnitOfWork(self.dependencies.database) as uow:
            return self.execute_in_uow(uow, command)

    def execute_in_uow(
        self,
        uow: UnitOfWork,
        command: SendMessageCommand,
    ) -> CommandResult:
        """Borrow the caller transaction without commit, connection or retry."""
        account_id, conversation_id, key = command.account_id, command.conversation_id, command.key
        content, agent_strategy = command.content, command.agent_strategy
        file_ids, fixed_html = command.file_ids, command.fixed_html
        if agent_strategy not in {"react", "plan_and_execute"}:
            raise ValueError("unsupported agent strategy")
        request_id = message_request_id(key)
        payload: dict[str, Any] = {"conversation_id": str(conversation_id), "content": content}
        if fixed_html is not None:
            payload["fixed_html"] = fixed_html
        if agent_strategy != "react":
            payload["agent_strategy"] = agent_strategy
        if file_ids:
            payload["file_ids"] = [str(file_id) for file_id in file_ids]
        existing = self.dependencies.claim(uow, account_id, "send_message", key, payload)
        if existing:
            return existing
        conversation = self.dependencies.conversations.lock_active(uow, account_id, conversation_id)
        if not conversation:
            raise ResourceNotFound()
        retained = self.dependencies.messages.find_initial_by_client_request(
            uow, account_id, request_id
        )
        if retained:
            if retained["conversation_id"] != conversation_id or retained["content"] != content:
                raise IdempotencyConflict()
            result = ChatProjection.send_result(uow, retained["run_id"])
            if result["run"]["agent_strategy"] != agent_strategy or sorted(
                item["file_id"] for item in result["user_message"]["files"]
            ) != sorted(str(file_id) for file_id in file_ids):
                raise IdempotencyConflict()
            self.dependencies.complete(uow, account_id, "send_message", key, 202, result)
            return CommandResult(202, result)
        self.dependencies.admission_policy.admit_in_locked_conversation(uow, conversation_id)
        if len(set(file_ids)) != len(file_ids):
            raise FileAlreadyBound()
        input_files = self.dependencies.files.lock_ready_inputs(
            uow, account_id, conversation_id, file_ids
        )
        if len(input_files) != len(file_ids):
            scoped_count = (
                uow.execute(
                    "SELECT count(*) AS count FROM stored_files WHERE account_id=%s "
                    "AND file_id=ANY(%s)",
                    (account_id, list(file_ids)),
                ).fetchone()["count"]
                if file_ids
                else 0
            )
            if scoped_count != len(file_ids):
                raise ResourceNotFound()
            raise FileNotReady()
        file_authorities = []
        for file in input_files:
            authority = self._explicit_file_authority(uow, account_id, conversation_id, file)
            if authority is None:
                raise ResourceDenied("file is not authorized for this Conversation")
            file_authorities.append(authority)
        session_id = None
        allocated = self.dependencies.conversations.allocate_messages(
            uow, account_id, conversation_id, 2
        )
        user_message_id, run_id, assistant_message_id = uuid7(), uuid7(), uuid7()
        workflow_id = f"hpagent-web-run-{run_id}"
        self.dependencies.messages.insert_user(
            uow, user_message_id, account_id, conversation_id, content, allocated - 1, request_id
        )
        self.dependencies.runs.insert_chat(
            uow,
            build_chat_run_insert(
                run_id,
                account_id,
                conversation_id,
                session_id,
                user_message_id,
                workflow_id,
                allocated - 1,
                agent_strategy=agent_strategy,
                fixed_html=fixed_html,
                agent_policy=self.dependencies.agent_policy.to_snapshot(),
            ),
        )
        self.dependencies.messages.insert_assistant(
            uow, assistant_message_id, account_id, conversation_id, allocated, run_id
        )
        self.dependencies.files.bind_inputs(
            uow, account_id, conversation_id, user_message_id, run_id, input_files
        )
        for file, authority in zip(input_files, file_authorities, strict=True):
            basis, node_id, grant_id = authority
            uow.execute(
                "INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,"
                "node_id,basis,grant_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (uuid7(), account_id, run_id, file["file_id"], node_id, basis, grant_id),
            )
            if basis == "explicit_attachment":
                uow.execute(
                    "INSERT INTO conversation_resource_files(account_id,conversation_id,file_id) "
                    "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (account_id, conversation_id, file["file_id"]),
                )
        self.dependencies.resource_policy.snapshot_in_uow(uow, account_id, run_id, conversation_id)
        self.dependencies.create_budget(uow, run_id, account_id, conversation_id)
        self.dependencies.enqueue_run_event(uow, account_id, conversation_id, run_id, "start_run")
        result = ChatProjection.send_result(uow, run_id)
        self.dependencies.complete(uow, account_id, "send_message", key, 202, result)
        log_event(
            logger,
            logging.INFO,
            "run_created",
            "run",
            run_id=str(run_id),
            conversation_id=str(conversation_id),
            session_id=str(session_id),
            status="started",
        )
        return CommandResult(202, result)

    def _explicit_file_authority(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, file: dict[str, Any]
    ) -> tuple[str, UUID | None, UUID | None] | None:
        if uow.execute(
            "SELECT 1 FROM conversation_resource_files WHERE account_id=%s AND conversation_id=%s AND file_id=%s AND available",
            (account_id, conversation_id, file["file_id"]),
        ).fetchone():
            return ("explicit_attachment", None, None)
        if file["conversation_id"] == conversation_id:
            available = uow.execute(
                "SELECT available FROM conversation_resource_files WHERE account_id=%s "
                "AND conversation_id=%s AND file_id=%s",
                (account_id, conversation_id, file["file_id"]),
            ).fetchone()
            return (
                ("explicit_attachment", None, None)
                if available is None or available["available"]
                else None
            )
        if (
            file.get("source_run_id") is not None
            and uow.execute(
                "SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s AND conversation_id=%s",
                (account_id, file["source_run_id"], conversation_id),
            ).fetchone()
        ):
            available = uow.execute(
                "SELECT available FROM conversation_resource_files WHERE account_id=%s "
                "AND conversation_id=%s AND file_id=%s",
                (account_id, conversation_id, file["file_id"]),
            ).fetchone()
            return (
                ("explicit_attachment", None, None)
                if available is None or available["available"]
                else None
            )
        nodes = uow.execute(
            "SELECT n.node_id FROM workspace_nodes n "
            "LEFT JOIN persistent_file_destinations d ON d.account_id=n.account_id "
            "AND d.destination_id=n.destination_id WHERE n.account_id=%s "
            "AND COALESCE(n.file_id,d.current_file_id)=%s "
            "AND n.deleted_at IS NULL ORDER BY n.node_id",
            (account_id, file["file_id"]),
        ).fetchall()
        for row in nodes:
            grants = self.dependencies.resource_policy._grants(
                uow, account_id, "conversation", conversation_id, row["node_id"], "read_content"
            )
            if grants:
                return ("workspace_grant", row["node_id"], grants[0]["grant_id"])
        return None
