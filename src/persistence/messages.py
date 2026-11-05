"""Ownership-scoped repositories.  No method accepts a bare public resource ID."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .uow import UnitOfWork


class MessageRepository:
    def context_messages(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, watermark: int
    ) -> list[dict[str, Any]]:
        """Load the frozen, model-visible Web history for exactly one Conversation."""
        return list(
            uow.execute(
                "SELECT message_id,role,status,content,sequence,origin,produced_by_run_id FROM messages "
                "WHERE account_id=%s AND conversation_id=%s AND sequence<=%s "
                "AND ((role='user' AND status='accepted') OR "
                "(role='assistant' AND status='completed')) ORDER BY sequence",
                (account_id, conversation_id, watermark),
            ).fetchall()
        )

    def find_initial_by_client_request(
        self, uow: UnitOfWork, account_id: UUID, client_request_id: UUID
    ):
        return uow.execute(
            "SELECT m.message_id,m.conversation_id,m.content,m.sequence AS user_sequence,"
            "r.run_id,r.session_id,r.status AS run_status,r.trigger_message_id,"
            "a.message_id AS assistant_message_id,a.sequence AS assistant_sequence,"
            "a.status AS assistant_status "
            "FROM messages m JOIN runs r ON r.trigger_message_id=m.message_id "
            "AND r.retry_of_run_id IS NULL JOIN messages a ON a.produced_by_run_id=r.run_id "
            "WHERE m.account_id=%s "
            "AND m.client_request_id=%s",
            (account_id, client_request_id),
        ).fetchone()

    def insert_user(
        self,
        uow: UnitOfWork,
        message_id: UUID,
        account_id: UUID,
        conversation_id: UUID,
        content: str,
        sequence: int,
        client_request_id: UUID,
    ) -> None:
        uow.execute(
            "INSERT INTO messages(message_id,account_id,conversation_id,role,status,content,"
            "sequence,client_request_id) VALUES (%s,%s,%s,'user','accepted',%s,%s,%s)",
            (message_id, account_id, conversation_id, content, sequence, client_request_id),
        )

    def insert_assistant(
        self,
        uow: UnitOfWork,
        message_id: UUID,
        account_id: UUID,
        conversation_id: UUID,
        sequence: int,
        run_id: UUID,
    ) -> None:
        uow.execute(
            "INSERT INTO messages(message_id,account_id,conversation_id,role,status,sequence,"
            "produced_by_run_id) VALUES (%s,%s,%s,'assistant','pending',%s,%s)",
            (message_id, account_id, conversation_id, sequence, run_id),
        )

    def set_terminal(
        self, uow: UnitOfWork, run_id: UUID, status: str, content: str | None = None
    ) -> None:
        uow.execute(
            "UPDATE messages SET status=%s,content=COALESCE(%s,content),"
            "completed_at=GREATEST(now(),created_at) "
            "WHERE produced_by_run_id=%s AND status='pending'",
            (status, content, run_id),
        )

    def user_for_run(self, uow, run_id):
        return uow.execute(
            "SELECT m.* FROM messages m JOIN runs r ON r.trigger_message_id=m.message_id WHERE r.run_id=%s",
            (run_id,),
        ).fetchone()

    def assistant_for_run(self, uow, run_id):
        return uow.execute(
            "SELECT * FROM messages WHERE produced_by_run_id=%s", (run_id,)
        ).fetchone()

    def bound_files(self, uow, message_id):
        return uow.execute(
            "SELECT sf.* FROM message_files mf JOIN stored_files sf "
            "ON sf.account_id=mf.account_id AND sf.file_id=mf.file_id "
            "WHERE mf.message_id=%s ORDER BY mf.ordinal",
            (message_id,),
        ).fetchall()

    def set_origin(self, uow, account_id, message_id, origin_json):
        uow.execute(
            "UPDATE messages SET origin=%s::jsonb WHERE account_id=%s AND message_id=%s",
            (origin_json, account_id, message_id),
        )
