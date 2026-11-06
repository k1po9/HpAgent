"""SQL for mandate identity, immutable requirements and business events."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from uuid6 import uuid7

from persistence.uow import UnitOfWork
from work_domain.serialization import to_json_value


class WorkEventRepository:
    @staticmethod
    def append(
        uow: UnitOfWork,
        work: dict,
        event_type: str,
        *,
        command_id: UUID | None = None,
        run_id: UUID | None = None,
        **payload: Any,
    ) -> UUID:
        event_id = uuid7()
        uow.execute(
            "INSERT INTO work_events(event_id,account_id,work_id,event_seq,event_type,"
            "requirement_revision,command_id,run_id,bounded_payload) "
            "SELECT %s,%s,%s,COALESCE(max(event_seq),0)+1,%s,%s,%s,%s,%s::jsonb "
            "FROM work_events WHERE account_id=%s AND work_id=%s",
            (
                event_id,
                work["account_id"],
                work["work_id"],
                event_type,
                work["current_requirement_revision"],
                command_id,
                run_id,
                json.dumps({"schema_version": 1, **to_json_value(payload)}),
                work["account_id"],
                work["work_id"],
            ),
        )
        return event_id
