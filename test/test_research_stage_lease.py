"""A lost Research lease stops its owner; cancellation also drains renewal work."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError

from research_activities.runtime import ResearchActivities
from run_domain.execution import LeaseConflict, StaleFencingToken
from run_domain.execution_context import execution_fence


def activities():
    instance = ResearchActivities(None, None, None, None, None)
    store = Mock()
    store.run_identity.return_value = {"account_id": str(uuid4()), "execution_id": str(uuid4())}
    store.acquire_segment.return_value = 7
    instance.store = store
    return instance, store


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [StaleFencingToken("revoked"), RuntimeError("database lost")])
async def test_renewal_failure_cancels_stage_before_releasing_ownership(failure):
    instance, store = activities()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def stage(*args):
        assert execution_fence.get() is not None
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def renew(*args):
        await started.wait()
        raise failure

    instance._run_stage_in_execution = stage
    instance._maintain_stage_lease = renew
    store.release_segment.side_effect = lambda request: assert_cancelled(cancelled)
    expected = ApplicationError if isinstance(failure, StaleFencingToken) else RuntimeError
    with pytest.raises(expected):
        await asyncio.wait_for(instance._run_stage(uuid4(), "Fetch:1", lambda: None), 2)
    store.release_segment.assert_called_once()
    assert execution_fence.get() is None


def assert_cancelled(event):
    assert event.is_set()


@pytest.mark.asyncio
async def test_stage_success_drains_renewal_before_release():
    instance, store = activities()
    renew_started = asyncio.Event()
    renew_stopped = asyncio.Event()

    async def renew(*args):
        renew_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            renew_stopped.set()

    async def stage(*args):
        await renew_started.wait()
        return {"ref": "committed"}

    instance._run_stage_in_execution = stage
    instance._maintain_stage_lease = renew
    store.release_segment.side_effect = lambda request: assert_cancelled(renew_stopped)
    assert await instance._run_stage(uuid4(), "PublishArtifact", lambda: None) == {"ref": "committed"}
    store.release_segment.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_waiting_for_another_owner_never_starts_stage():
    instance, store = activities()
    conflict = asyncio.Event()
    loop = asyncio.get_running_loop()
    stage = AsyncMock()
    instance._run_stage_in_execution = stage

    def acquire(request):
        loop.call_soon_threadsafe(conflict.set)
        raise LeaseConflict("owned")

    store.acquire_segment.side_effect = acquire
    task = asyncio.create_task(instance._run_stage(uuid4(), "WorkspaceSave", lambda: None))
    await asyncio.wait_for(conflict.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stage.assert_not_called()
    store.release_segment.assert_called_once()
