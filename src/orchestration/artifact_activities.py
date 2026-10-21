"""Short fixed-parent adapters; local generation runs in its own Execution."""

import asyncio
from uuid import UUID

from temporalio import activity

from agent_workflows.capability_contracts import CapabilityExecutionInput


class ArtifactActivities:
    def __init__(self, build_service):
        self._build_service = build_service

    @activity.defn(name="prepare_artifact_build_activity")
    async def prepare(self, request: dict) -> CapabilityExecutionInput:
        return await asyncio.to_thread(self._build_service.prepare, UUID(request["run_id"]))

    @activity.defn(name="complete_artifact_build_activity")
    async def complete(self, request: CapabilityExecutionInput) -> dict:
        return await asyncio.to_thread(self._build_service.complete, request)
