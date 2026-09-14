"""Control fakes for Workflow-only tests; real PG gates use SegmentActivities."""

from temporalio import activity

from agent_workflows.lifecycle_contracts import (
    FinishWaitInput,
    SegmentInput,
    SegmentLease,
    WaitInput,
)

_token = 0


@activity.defn(name="acquire_agent_segment_activity")
async def acquire_segment(request: SegmentInput) -> SegmentLease:
    global _token
    _token += 1
    return SegmentLease(True, _token)


@activity.defn(name="release_agent_segment_activity")
async def release_segment(request: SegmentInput) -> None:
    pass


@activity.defn(name="begin_agent_wait_activity")
async def begin_wait(request: WaitInput) -> None:
    pass


@activity.defn(name="finish_agent_wait_activity")
async def finish_wait(request: FinishWaitInput) -> bool:
    return True


CONTROL_ACTIVITIES = [acquire_segment, release_segment, begin_wait, finish_wait]
