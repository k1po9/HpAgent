"""SQL for mandate identity, immutable requirements and business events."""

from __future__ import annotations

import json
from uuid import UUID

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound
from work_domain.models import digest


class WorkRequirementRepository:
    @staticmethod
    def get(uow: UnitOfWork, account_id: UUID, work_id: UUID, revision: int) -> dict:
        row = uow.execute(
            "SELECT * FROM work_requirements WHERE account_id=%s AND work_id=%s AND revision=%s",
            (account_id, work_id, revision),
        ).fetchone()
        if row is None:
            raise ResourceNotFound()
        return dict(row)

    @staticmethod
    def insert(
        uow: UnitOfWork,
        account_id: UUID,
        work_id: UUID,
        revision: int,
        value: dict,
        command_id: UUID,
        reason: str,
        source_message_id: UUID | None = None,
    ) -> None:
        uow.execute(
            "INSERT INTO work_requirements(account_id,work_id,revision,objective,constraints,"
            "acceptance_criteria,completion_mode,capability_key,spec,timing,resource_requests,"
            "deliverable_policy,created_by,source_message_id,command_id,change_reason,content_hash,deliverables) "
            "VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,"
            "%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)",
            (
                account_id,
                work_id,
                revision,
                value["objective"],
                json.dumps(value["constraints"]),
                json.dumps(value["acceptance_criteria"]),
                value["completion_mode"],
                value["capability_key"],
                json.dumps(value["spec"]),
                json.dumps(value["timing"]),
                json.dumps(value["resource_requests"]),
                json.dumps(value["deliverable_policy"]),
                "account",
                source_message_id,
                command_id,
                reason,
                digest(value),
                json.dumps(value["deliverables"])
                if value.get("deliverables") is not None
                else None,
            ),
        )
