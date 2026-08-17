from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound, VersionConflict

from .security import CursorCodec, CursorError


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def conversation_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": str(row["conversation_id"]),
        "title": row["title"],
        "status": row["status"],
        "last_message_seq": row["last_message_seq"],
        "metadata_version": row["metadata_version"],
        "created_at": timestamp(row["created_at"]),
        "updated_at": timestamp(row["updated_at"]),
    }


def message_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": str(row["message_id"]),
        "conversation_id": str(row["conversation_id"]),
        "role": row["role"],
        "status": row["status"],
        "content": row["content"],
        "sequence": row["sequence"],
        "client_request_id": (
            str(row["client_request_id"]) if row["client_request_id"] else None
        ),
        "produced_by_run_id": (
            str(row["produced_by_run_id"]) if row["produced_by_run_id"] else None
        ),
        "created_at": timestamp(row["created_at"]),
        "completed_at": timestamp(row["completed_at"]),
    }


def run_dto(row: dict[str, Any]) -> dict[str, Any]:
    failure = None
    if row["status"] == "failed":
        failure = {
            "code": row["failure_code"],
            "message": row["failure_message"]
            or "Agent 暂时无法完成本次请求，请稍后重试。",
            "retryable": True,
        }
    return {
        "run_id": str(row["run_id"]),
        "conversation_id": str(row["conversation_id"]),
        "session_id": str(row["session_id"]),
        "trigger_message_id": str(row["trigger_message_id"]),
        "retry_of_run_id": str(row["retry_of_run_id"]) if row["retry_of_run_id"] else None,
        "agent_strategy": str(row.get("agent_strategy") or "react"),
        "status": row["status"],
        "failure": failure,
        "version": row["version"],
        "created_at": timestamp(row["created_at"]),
        "started_at": timestamp(row["started_at"]),
        "finished_at": timestamp(row["finished_at"]),
        "updated_at": timestamp(row["updated_at"]),
    }


class QueryService:
    def __init__(self, database: object, cursors: CursorCodec):
        self.database = database
        self.cursors = cursors

    def get_conversation(self, account_id: UUID, conversation_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            conversation = uow.execute(
                "SELECT * FROM conversations WHERE account_id=%s AND conversation_id=%s "
                "AND status='active'",
                (account_id, conversation_id),
            ).fetchone()
            if not conversation:
                raise ResourceNotFound()
            active = uow.execute(
                "SELECT r.*,m.message_id AS m_message_id,m.conversation_id AS m_conversation_id,"
                "m.role AS m_role,m.status AS m_status,m.content AS m_content,"
                "m.sequence AS m_sequence,m.client_request_id AS m_client_request_id,"
                "m.produced_by_run_id AS m_produced_by_run_id,m.created_at AS m_created_at,"
                "m.completed_at AS m_completed_at FROM runs r JOIN messages m "
                "ON m.produced_by_run_id=r.run_id WHERE r.account_id=%s "
                "AND r.conversation_id=%s AND r.status IN ('queued','running','cancelling')",
                (account_id, conversation_id),
            ).fetchone()
            return {
                "conversation": conversation_dto(conversation),
                "active_run": self._joined_snapshot(active) if active else None,
            }

    def list_conversations(
        self, account_id: UUID, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        before: tuple[datetime, UUID] | None = None
        if cursor:
            body = self.cursors.decode(cursor)
            if (
                body.get("v") != 1
                or body.get("endpoint") != "conversations"
                or body.get("account_scope") != str(account_id)
                or body.get("limit") != limit
            ):
                raise CursorError("invalid_cursor")
            try:
                before = (datetime.fromisoformat(body["updated_at"]), UUID(body["id"]))
            except (KeyError, ValueError, TypeError):
                raise CursorError("invalid_cursor") from None
        with UnitOfWork(self.database) as uow:
            params: list[Any] = [account_id]
            condition = ""
            if before:
                condition = " AND (updated_at,conversation_id)<(%s,%s)"
                params.extend(before)
            params.append(limit + 1)
            rows = list(
                uow.execute(
                    "SELECT * FROM conversations WHERE account_id=%s AND status='active'"
                    + condition
                    + " ORDER BY updated_at DESC,conversation_id DESC LIMIT %s",
                    tuple(params),
                ).fetchall()
            )
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = self.cursors.encode(
                {
                    "v": 1,
                    "endpoint": "conversations",
                    "account_scope": str(account_id),
                    "limit": limit,
                    "updated_at": last["updated_at"].isoformat(),
                    "id": str(last["conversation_id"]),
                }
            )
        return {
            "items": [conversation_dto(row) for row in items],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    def list_messages(
        self, account_id: UUID, conversation_id: UUID, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        before_sequence: int | None = None
        if cursor:
            body = self.cursors.decode(cursor)
            if (
                body.get("v") != 1
                or body.get("endpoint") != "conversation_messages"
                or body.get("account_scope") != str(account_id)
                or body.get("resource_id") != str(conversation_id)
                or body.get("limit") != limit
                or not isinstance(body.get("before_sequence"), int)
            ):
                raise CursorError("invalid_cursor")
            before_sequence = body["before_sequence"]
        with UnitOfWork(self.database) as uow:
            conversation = uow.execute(
                "SELECT last_message_seq FROM conversations WHERE account_id=%s "
                "AND conversation_id=%s AND status='active'",
                (account_id, conversation_id),
            ).fetchone()
            if not conversation:
                raise ResourceNotFound()
            params: list[Any] = [account_id, conversation_id]
            condition = ""
            if before_sequence is not None:
                condition = " AND sequence<%s"
                params.append(before_sequence)
            params.append(limit + 1)
            rows = list(
                uow.execute(
                    "SELECT * FROM messages WHERE account_id=%s AND conversation_id=%s"
                    + condition
                    + " ORDER BY sequence DESC LIMIT %s",
                    tuple(params),
                ).fetchall()
            )
        has_more = len(rows) > limit
        selected = rows[:limit]
        next_cursor = None
        if has_more and selected:
            next_cursor = self.cursors.encode(
                {
                    "v": 1,
                    "endpoint": "conversation_messages",
                    "account_scope": str(account_id),
                    "resource_id": str(conversation_id),
                    "limit": limit,
                    "before_sequence": selected[-1]["sequence"],
                }
            )
        selected.reverse()
        return {
            "items": [message_dto(row) for row in selected],
            "next_cursor": next_cursor,
            "has_more": has_more,
            "conversation_last_message_seq": conversation["last_message_seq"],
        }

    def get_run(self, account_id: UUID, run_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT r.*,m.message_id AS m_message_id,m.conversation_id AS m_conversation_id,"
                "m.role AS m_role,m.status AS m_status,m.content AS m_content,"
                "m.sequence AS m_sequence,m.client_request_id AS m_client_request_id,"
                "m.produced_by_run_id AS m_produced_by_run_id,m.created_at AS m_created_at,"
                "m.completed_at AS m_completed_at FROM runs r JOIN messages m "
                "ON m.produced_by_run_id=r.run_id WHERE r.account_id=%s AND r.run_id=%s",
                (account_id, run_id),
            ).fetchone()
            if not row:
                raise ResourceNotFound()
            return self._joined_snapshot(row)

    def rename_conversation(
        self, account_id: UUID, conversation_id: UUID, version: int, title: str
    ) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "UPDATE conversations SET title=%s,metadata_version=metadata_version+1,"
                "updated_at=now() WHERE account_id=%s AND conversation_id=%s "
                "AND status='active' AND metadata_version=%s RETURNING *",
                (title, account_id, conversation_id, version),
            ).fetchone()
            if row:
                return conversation_dto(row)
            current = uow.execute(
                "SELECT metadata_version FROM conversations WHERE account_id=%s "
                "AND conversation_id=%s AND status='active'",
                (account_id, conversation_id),
            ).fetchone()
            if not current:
                raise ResourceNotFound()
            raise VersionConflict(current["metadata_version"])

    @staticmethod
    def _joined_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        message = {
            "message_id": row["m_message_id"],
            "conversation_id": row["m_conversation_id"],
            "role": row["m_role"],
            "status": row["m_status"],
            "content": row["m_content"],
            "sequence": row["m_sequence"],
            "client_request_id": row["m_client_request_id"],
            "produced_by_run_id": row["m_produced_by_run_id"],
            "created_at": row["m_created_at"],
            "completed_at": row["m_completed_at"],
        }
        return {"run": run_dto(row), "assistant_message": message_dto(message)}
