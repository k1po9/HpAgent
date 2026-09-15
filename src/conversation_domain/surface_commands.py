"""Surface binding/receipt transaction around canonical Conversation commands."""
from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid5

from persistence.command_result import CommandResult
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import (
    ConversationBusy,
    IdempotencyConflict,
    ResourceNotFound,
    RunNotCancellable,
)

from .commands import CommandService


class IdentityNotBound(Exception):
    """No currently verified identity and active account; never an infrastructure error."""


def stable_key(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SurfaceConversationCommands:
    def __init__(self, commands: CommandService):
        self.commands = commands

    @retryable_transaction
    def execute(
        self, *, provider: str, subject: str, route: dict, message_key: str,
        content: str, origin: dict, operation: str = "message", target_run: UUID | None = None,
    ) -> CommandResult:
        if operation not in {"message", "cancel"}:
            raise ValueError("unsupported surface command")
        digest = bytes.fromhex(stable_key({
            "route": route, "subject": subject, "content": content,
            "operation": operation, "target_run": str(target_run) if target_run else None,
        }))
        with UnitOfWork(self.commands.database_url) as uow:
            identity = uow.execute(
                "SELECT b.account_id FROM identity_bindings b JOIN accounts a "
                "ON a.account_id=b.account_id WHERE b.provider=%s AND b.normalized_subject_id=%s "
                "AND b.status='active' AND b.verified_at IS NOT NULL AND b.revoked_at IS NULL "
                "AND a.status='active'", (provider, subject),
            ).fetchone()
            if identity is None:
                raise IdentityNotBound()
            account_id = identity["account_id"]
            # Serialize this external identity before inspecting receipts/binding.
            # This is a short PG lock, never an account mailbox or execution lease.
            lock_key = int.from_bytes(hashlib.sha256(f"{account_id}:{message_key}".encode()).digest()[:8],
                                      "big", signed=True)
            uow.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            previous = uow.execute(
                "SELECT * FROM conversation_ingress_receipts WHERE account_id=%s AND message_key=%s",
                (account_id, message_key),
            ).fetchone()
            if previous:
                if bytes(previous["request_hash"]) != digest:
                    raise IdempotencyConflict()
                return CommandResult(previous["response_status"], previous["response_body"], replayed=True)
            binding_key = stable_key(route)
            binding = uow.execute(
                "SELECT conversation_id FROM conversation_bindings WHERE account_id=%s AND binding_key=%s",
                (account_id, binding_key),
            ).fetchone()
            if binding:
                cid = binding["conversation_id"]
            else:
                # Independent first deliveries serialize through the existing
                # Conversation row/unique constraint, not an in-memory registry.
                cid = uuid5(account_id, "surface-conversation:" + binding_key)
                uow.execute(
                    "INSERT INTO conversations(account_id,conversation_id) VALUES (%s,%s) "
                    "ON CONFLICT (conversation_id) DO NOTHING", (account_id, cid),
                )
                uow.execute(
                    "INSERT INTO conversation_bindings(account_id,binding_key,conversation_id,route) "
                    "VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (account_id,binding_key) DO NOTHING",
                    (account_id, binding_key, cid, json.dumps(route)),
                )
                cid = uow.execute(
                    "SELECT conversation_id FROM conversation_bindings WHERE account_id=%s AND binding_key=%s",
                    (account_id, binding_key),
                ).fetchone()["conversation_id"]
            if not self.commands.conversations.lock_active(uow, account_id, cid):
                raise ResourceNotFound()
            try:
                # A domain rejection rolls back command writes, then the outer
                # transaction records the stable rejection receipt.
                with uow.connection.transaction():
                    if operation == "message":
                        result = self.commands._send_message_in_uow(
                            uow, account_id, cid, message_key, content,
                        )
                        uow.execute(
                            "UPDATE messages SET origin=%s::jsonb WHERE account_id=%s AND message_id=%s",
                            (json.dumps(origin), account_id, UUID(result["message_id"])),
                        )
                    else:
                        run = uow.execute(
                            "SELECT run_id FROM runs WHERE account_id=%s AND conversation_id=%s "
                            + ("AND run_id=%s" if target_run else
                               "AND status IN ('queued','running','cancelling')"),
                            (account_id, cid, target_run) if target_run else (account_id, cid),
                        ).fetchone()
                        if not run:
                            result = CommandResult(409, {"code": "no_matching_run"})
                        else:
                            result = self.commands._cancel_run_in_uow(
                                uow, account_id, run["run_id"], message_key,
                            )
            except ConversationBusy:
                result = CommandResult(409, {"code": "conversation_busy"})
            except RunNotCancellable:
                result = CommandResult(409, {"code": "run_not_cancellable"})
            body = {**result.body, "conversation_id": str(cid), "account_id": str(account_id)}
            uow.execute(
                "INSERT INTO conversation_ingress_receipts(account_id,message_key,conversation_id,"
                "request_hash,response_status,response_body) VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                (account_id, message_key, cid, digest, result.response_status, json.dumps(body)),
            )
            return CommandResult(result.response_status, body)
