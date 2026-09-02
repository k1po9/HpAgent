from __future__ import annotations

from uuid import uuid4

import pytest

from orchestration.research_schedule import ResearchScheduleManager


class _Handle:
    def __init__(self, client, schedule_id):
        self.client, self.schedule_id = client, schedule_id

    async def delete(self):
        if self.schedule_id not in self.client.schedules:
            raise RuntimeError("not found")
        del self.client.schedules[self.schedule_id]


class _Client:
    def __init__(self):
        self.schedules = {}

    def get_schedule_handle(self, schedule_id):
        return _Handle(self, schedule_id)

    async def create_schedule(self, schedule_id, schedule):
        if schedule_id in self.schedules:
            raise RuntimeError("duplicate")
        self.schedules[schedule_id] = schedule


@pytest.mark.asyncio
async def test_research_schedule_reconcile_create_update_disable_is_idempotent():
    task_id, account_id = uuid4(), uuid4()
    client = _Client()
    manager = ResearchScheduleManager("unused", client)
    pending = [{
        "task_id": task_id,
        "account_id": account_id,
        "schedule_type": "daily",
        "schedule_timezone": "Asia/Shanghai",
        "schedule_expression": "08:30",
        "schedule_enabled": True,
        "schedule_version": 2,
    }]
    applied = []
    manager._pending = lambda: list(pending)  # type: ignore[method-assign]
    manager._mark_applied = lambda task, version: applied.append((task, version))  # type: ignore[method-assign]

    assert await manager.reconcile_once() == 1
    schedule_id = manager.schedule_id(task_id)
    assert schedule_id in client.schedules
    assert applied == [(task_id, 2)]

    pending.clear()
    assert await manager.reconcile_once() == 0
    assert len(client.schedules) == 1

    pending.append({
        "task_id": task_id,
        "account_id": account_id,
        "schedule_type": "manual",
        "schedule_timezone": "UTC",
        "schedule_expression": None,
        "schedule_enabled": False,
        "schedule_version": 3,
    })
    assert await manager.reconcile_once() == 1
    assert client.schedules == {}
