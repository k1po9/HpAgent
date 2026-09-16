"""Temporal cancellation and deadline control for capability execution."""

import asyncio
from datetime import UTC, datetime

from temporalio import activity


class TemporalActivityControl:
    """ExecutionControl backed by the current Temporal Activity cancellation token."""

    def cancelled(self) -> bool:
        try:
            return bool(activity.is_cancelled())
        except RuntimeError:
            # Direct unit tests have no Activity context; production always does.
            return False

    @property
    def deadline(self) -> datetime:
        try:
            info = activity.info()
        except RuntimeError:
            return datetime.max.replace(tzinfo=UTC)
        candidates: list[datetime] = []
        if info.start_to_close_timeout is not None:
            candidates.append(info.started_time + info.start_to_close_timeout)
        if info.schedule_to_close_timeout is not None:
            candidates.append(info.scheduled_time + info.schedule_to_close_timeout)
        return min(candidates) if candidates else datetime.max.replace(tzinfo=UTC)

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise asyncio.CancelledError

    async def heartbeat(self, phase: str) -> None:
        if phase not in {
            "starting",
            "waiting_for_workspace_lock",
            "selecting_tools",
            "generating",
            "executing_tool",
        }:
            raise ValueError("unsupported execution heartbeat phase")
        try:
            activity.heartbeat({"schema_version": 1, "phase": phase})
        except RuntimeError:
            return
