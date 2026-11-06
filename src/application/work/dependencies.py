"""One command boundary shared by API and application tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from uuid6 import uuid7

from common.agent_policy import AgentPolicy
from persistence.command_result import CommandResult
from persistence.idempotency import IdempotencyRepository
from persistence.works import WorkRepository
from run_domain.execution import assert_execution_fence
from web_domain.errors import IdempotencyConflict, ResourceNotFound
from work_domain.errors import WorkConflict
from work_domain.models import digest
from work_domain.serialization import to_json_value

from .queries import WorkQueries


class WorkCommandDependencies:
    def __init__(
        self,
        database: object,
        *,
        budget_mode: str = "enforce",
        agent_policy: AgentPolicy | None = None,
    ):
        self.database = database
        self.budget_mode = budget_mode
        self.agent_policy = agent_policy or AgentPolicy()
        self.repo = WorkRepository()
        self.idempotency = IdempotencyRepository()

        self.queries = WorkQueries(database, self.repo)

    def claim(self, uow, account_id, operation, key, payload):
        assert_execution_fence(uow)
        request_hash = digest(payload)
        row = self.idempotency.claim(
            uow,
            uuid7(),
            account_id,
            operation,
            key,
            request_hash,
            datetime.now(UTC) + timedelta(days=7),
        )
        if row is not None:
            if bytes(row["request_hash"]) != request_hash:
                raise IdempotencyConflict()
            if row["status"] != "completed":
                raise WorkConflict({}, "command_in_progress")
            return CommandResult(
                int(row["response_status"]), dict(row["response_body"]), replayed=True
            ), None
        row = uow.execute(
            "SELECT idempotency_command_id FROM idempotency_commands "
            "WHERE account_id=%s AND operation=%s AND idempotency_key=%s",
            (account_id, operation, key),
        ).fetchone()
        return None, row["idempotency_command_id"]

    def complete(
        self, uow, account_id, operation, key, work_id, status=200, **extra
    ) -> CommandResult:
        body = {"work": self.queries.snapshot_in_uow(uow, account_id, work_id), **to_json_value(extra)}
        self.idempotency.complete(uow, account_id, operation, key, status, json.dumps(body))
        return CommandResult(status, body)

    @staticmethod
    def link(uow, work, conversation_id, command, source_message_id=None):
        if not uow.execute(
            "SELECT 1 FROM conversations WHERE account_id=%s AND conversation_id=%s",
            (work["account_id"], conversation_id),
        ).fetchone():
            raise ResourceNotFound()
        WorkRepository.link_conversation(
            uow, work["account_id"], work["work_id"], conversation_id, command, source_message_id
        )

    def locked(self, uow, account_id, work_id, row_version):
        work = self.repo.get(uow, account_id, work_id, lock=True)
        if work["row_version"] != row_version:
            raise WorkConflict(work)
        return work
