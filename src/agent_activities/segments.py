"""Short PG-only lifecycle Activities, with no workspace/model resources."""

import asyncio

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_workflows.lifecycle_contracts import (
    LIFECYCLE_SCHEMA_VERSION,
    FinishWaitInput,
    SegmentInput,
    SegmentLease,
    WaitInput,
)

from .store import AgentDataStore, LeaseConflict, RunNotExecutable, SegmentClosed


class SegmentActivities:
    def __init__(self, store: AgentDataStore):
        self.store = store

    @staticmethod
    def _check(request):
        if request.schema_version != LIFECYCLE_SCHEMA_VERSION:
            raise ApplicationError("unsupported lifecycle schema", non_retryable=True)

    @activity.defn(name="acquire_agent_segment_activity")
    async def acquire(self, request: SegmentInput) -> SegmentLease:
        self._check(request)
        try:
            token = await asyncio.to_thread(self.store.acquire_segment, request)
            return SegmentLease(True, token)
        except LeaseConflict:
            return SegmentLease(False)
        except (RunNotExecutable, SegmentClosed) as exc:
            raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc

    @activity.defn(name="release_agent_segment_activity")
    async def release(self, request: SegmentInput) -> None:
        self._check(request)
        await asyncio.to_thread(self.store.release_segment, request)

    @activity.defn(name="begin_agent_wait_activity")
    async def begin_wait(self, request: WaitInput) -> None:
        self._check(request)
        try:
            await asyncio.to_thread(self.store.begin_wait, request)
        except (RunNotExecutable, LeaseConflict) as exc:
            raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc

    @activity.defn(name="finish_agent_wait_activity")
    async def finish_wait(self, request: FinishWaitInput) -> bool:
        self._check(request.wait)
        return await asyncio.to_thread(self.store.finish_wait, request)
