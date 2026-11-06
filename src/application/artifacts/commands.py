from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from uuid6 import uuid7

from application.chat.commands import SendMessageCommand
from application.chat.dependencies import ChatCommandDependencies
from application.chat.send_message import SendMessage
from persistence.command_result import CommandResult
from persistence.idempotency import IdempotencyRepository
from persistence.uow import UnitOfWork, retryable_transaction
from web_artifacts.queries import ArtifactQueries
from web_domain.errors import ConversationBusy, IdempotencyConflict, ResourceNotFound


def _digest(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).digest()


def create_artifact_chat_sender(database: object) -> SendMessage:
    """Build the Artifact Chat sender with the existing observe-mode budget defaults."""
    return SendMessage(
        ChatCommandDependencies(
            database,
            budget_mode="observe",
            budget_policy_version="file-p0-v1",
            final_response_reserve_tokens=1024,
        )
    )


class ArtifactCommands(ArtifactQueries):
    """Own the Artifact/Version and HTML Run write transaction."""

    def __init__(self, database: object, sender: SendMessage):
        super().__init__(database)
        self.idempotency = IdempotencyRepository()
        self.sender = sender

    @retryable_transaction
    def create_artifact(
        self,
        account_id: UUID,
        source_message_id: UUID,
        key: str,
        instruction: str | None = None,
    ) -> CommandResult:
        instruction = self._instruction(instruction)
        payload = {"source_message_id": str(source_message_id), "instruction": instruction}
        with UnitOfWork(self.database) as uow:
            replay = self._claim(uow, account_id, "create_artifact", key, payload)
            if replay:
                return replay
            source = uow.execute(
                "SELECT * FROM messages WHERE account_id=%s AND message_id=%s FOR UPDATE",
                (account_id, source_message_id),
            ).fetchone()
            if not source:
                raise ResourceNotFound()
            if (
                source["role"] != "assistant"
                or source["status"] != "completed"
                or not str(source["content"] or "").strip()
            ):
                raise ValueError("artifact_source_invalid")
            artifact_id, version_id, event_id = uuid7(), uuid7(), uuid7()
            title = self._title(str(source["content"]))
            uow.execute(
                "INSERT INTO artifacts(artifact_id,account_id,conversation_id,"
                "source_message_id,title) VALUES (%s,%s,%s,%s,%s)",
                (artifact_id, account_id, source["conversation_id"], source_message_id, title),
            )
            uow.execute(
                "INSERT INTO artifact_versions(artifact_version_id,artifact_id,account_id,"
                "version,instruction,source_markdown) VALUES (%s,%s,%s,1,%s,%s)",
                (version_id, artifact_id, account_id, instruction, source["content"]),
            )
            self._enqueue(
                uow,
                event_id,
                account_id,
                source["conversation_id"],
                artifact_id,
                version_id,
                "create_artifact",
                key,
            )
            body = self._detail(uow, account_id, artifact_id)
            self._complete(uow, account_id, "create_artifact", key, 202, body)
            return CommandResult(202, body)

    @retryable_transaction
    def create_version(
        self,
        account_id: UUID,
        artifact_id: UUID,
        key: str,
        instruction: str,
    ) -> CommandResult:
        instruction = cast(str, self._instruction(instruction, required=True))
        payload = {"artifact_id": str(artifact_id), "instruction": instruction}
        with UnitOfWork(self.database) as uow:
            replay = self._claim(uow, account_id, "create_artifact_version", key, payload)
            if replay:
                return replay
            artifact = uow.execute(
                "SELECT * FROM artifacts WHERE account_id=%s AND artifact_id=%s FOR UPDATE",
                (account_id, artifact_id),
            ).fetchone()
            if not artifact:
                raise ResourceNotFound()
            latest = uow.execute(
                "SELECT artifact_version_id,version,source_markdown FROM artifact_versions "
                "WHERE account_id=%s AND artifact_id=%s ORDER BY version DESC LIMIT 1",
                (account_id, artifact_id),
            ).fetchone()
            parent = uow.execute(
                "SELECT artifact_version_id,source_markdown FROM artifact_versions WHERE account_id=%s "
                "AND artifact_id=%s AND status='completed' ORDER BY version DESC LIMIT 1",
                (account_id, artifact_id),
            ).fetchone()
            version_id, event_id = uuid7(), uuid7()
            number = int(latest["version"]) + 1 if latest else 1
            uow.execute(
                "INSERT INTO artifact_versions(artifact_version_id,artifact_id,account_id,"
                "version,parent_version_id,instruction,source_markdown) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    version_id,
                    artifact_id,
                    account_id,
                    number,
                    parent["artifact_version_id"] if parent else None,
                    instruction,
                    parent["source_markdown"]
                    if parent
                    else latest["source_markdown"]
                    if latest
                    else None,
                ),
            )
            self._enqueue(
                uow,
                event_id,
                account_id,
                artifact["conversation_id"],
                artifact_id,
                version_id,
                "create_artifact_version",
                key,
            )
            version = self._version_row(uow, account_id, version_id)
            body = {"artifact": self._artifact_dto(artifact), "version": self._version_dto(version)}
            self._complete(uow, account_id, "create_artifact_version", key, 202, body)
            return CommandResult(202, body)

    def _claim(
        self, uow: UnitOfWork, account_id: UUID, operation: str, key: str, payload: object
    ) -> CommandResult | None:
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
        if row["status"] == "completed":
            return CommandResult(
                int(row["response_status"]),
                cast(dict[str, Any], row["response_body"]),
                replayed=True,
            )
        raise ConversationBusy()

    def _complete(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation: str,
        key: str,
        status: int,
        body: dict[str, Any],
    ) -> None:
        self.idempotency.complete(uow, account_id, operation, key, status, json.dumps(body))

    def _enqueue(
        self, uow, event_id, account_id, conversation_id, artifact_id, version_id, operation, key
    ):
        if conversation_id is None:
            # An explicit standalone Artifact action is a finite Chat command too.
            conversation_id = uuid7()
            self.sender.dependencies.conversations.create(
                uow, conversation_id, account_id, "HTML artifact"
            )
        previous = uow.execute(
            "SELECT p.file_id FROM artifact_versions v JOIN artifact_versions p ON p.account_id=v.account_id AND p.artifact_version_id=v.parent_version_id WHERE v.account_id=%s AND v.artifact_version_id=%s AND p.status='completed'",
            (account_id, version_id),
        ).fetchone()
        inputs: tuple[UUID, ...] = ()
        if previous and previous["file_id"]:
            # Explicit user selection authorizes exactly these immutable bytes, never all prior inputs.
            file_id = previous["file_id"]
            revoked = uow.execute(
                "SELECT 1 FROM conversation_resource_files WHERE account_id=%s AND conversation_id=%s AND file_id=%s AND NOT available",
                (account_id, conversation_id, file_id),
            ).fetchone()
            if revoked:
                raise ResourceNotFound()
            uow.execute(
                "INSERT INTO conversation_resource_files(account_id,conversation_id,file_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (account_id, conversation_id, file_id),
            )
            inputs = (file_id,)
        # The explicit user action creates a finite chat Run, with normal slot and budget rules.
        from uuid import NAMESPACE_URL, uuid5

        self.sender.execute_in_uow(
            uow,
            SendMessageCommand(
                account_id,
                conversation_id,
                str(uuid5(NAMESPACE_URL, f"artifact:{account_id}:{operation}:{key}")),
                f"生成 HTML 成果 {artifact_id}，版本 {version_id}。",
                file_ids=inputs,
                fixed_html={"schema_version": 1, "artifact_version_id": str(version_id)},
            ),
        )

    @staticmethod
    def _instruction(value: str | None, required: bool = False) -> str | None:
        result = value.strip() if value else None
        if required and not result:
            raise ValueError("artifact_instruction_required")
        if result and len(result) > 4000:
            raise ValueError("artifact_instruction_too_long")
        return result

    @staticmethod
    def _title(markdown: str) -> str:
        for line in markdown.splitlines():
            value = line.lstrip("# ").strip()
            if value:
                return value[:200]
        return "Web Artifact"
