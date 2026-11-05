"""Explicit Work event, notification and Outbox orchestration in one UoW."""

import json

from uuid6 import uuid7

from persistence.outbox import OutboxRepository
from persistence.uow import UnitOfWork
from persistence.work_events import WorkEventRepository


def record_work_event(
    uow: UnitOfWork, work: dict, event_type: str, *, command_id=None, run_id=None, **payload
):
    event_id = WorkEventRepository.append(
        uow, work, event_type, command_id=command_id, run_id=run_id, **payload
    )
    from delivery.service import enqueue_event

    enqueue_event(uow, work, event_id, event_type, run_id)
    OutboxRepository().enqueue_work_event(
        uow,
        uuid7(),
        work["account_id"],
        f"work-event:{event_id}",
        work["work_id"],
        json.dumps({"work_id": str(work["work_id"]), "event_id": str(event_id), "version": 1}),
    )
    return event_id
