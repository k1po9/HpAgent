"""Deterministic test-only capability. No model, network, long-term save or external effects."""

import asyncio
import os

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from test_capability_contracts import definition

from agent_activities.capabilities import CapabilityActivities, CapabilityBinding
from agent_activities.fencing import fenced_activity
from agent_activities.segments import SegmentActivities
from agent_activities.store import AgentDataStore
from agent_workflows.capability import CapabilityInvocationWorkflow
from agent_workflows.capability_contracts import CapabilityExecutionInput
from agent_workflows.contracts import AGENT_TASK_QUEUE
from agent_workflows.tool_execution import ToolExecutionWorkflow
from application.tool_policy import ToolPolicy, tool_policy
from persistence.uow import UnitOfWork
from resources.capacity import CapacityService
from resources.run_budget import RunBudgetService
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import CapabilityRegistry, CapabilityResult
from support.capability_workflow import DeterministicCapabilityWorkflow


class ProbeActivities(CapabilityActivities):
    @activity.defn(name="test_capability_publish")
    @fenced_activity
    async def publish(self, request: CapabilityExecutionInput) -> str:
        previous = await asyncio.to_thread(
            self.store.begin_operation, request.operation_id, request.run_id, "tool"
        )
        if previous:
            return f"capability-result:{request.invocation_id}"
        budget = RunBudgetService(self.store.database_url)
        from uuid import UUID

        async with CapacityService(self.store.database_url).slot(UUID(request.run_id), "tool"):
            budget.reserve(UUID(request.run_id), request.operation_id, {"tool_calls": 1})
            with UnitOfWork(self.store.database_url) as uow:
                descriptor = self.invocation_context(uow, request)
            await asyncio.to_thread(
                self.commit_result,
                request,
                CapabilityResult("succeeded", descriptor["inputs"]["text"]),
                usage={"tool_calls": 1},
            )
        return f"capability-result:{request.invocation_id}"


def activities(database):
    store = AgentDataStore(database, lease_ttl_seconds=15)
    facts = CapabilityFacts(CapabilityRegistry((definition(),)))
    binding = CapabilityBinding("test.echo", 1, "DeterministicCapabilityWorkflow")
    configured = tool_policy().model_dump()
    configured["groups"]["test_echo"] = {
        "description": "隔离测试专用能力", "tools": [binding.tool_name], "exposure": "always",
    }
    for role in ("main", "worker"):
        configured["roles"][role]["groups"] += ("test_echo",)
    policy = ToolPolicy.model_validate(configured)
    return ProbeActivities(
        store, facts, (binding,), policy=policy,
    )


async def main():
    probe = activities(os.environ["WORKER_DATABASE_URL"])
    controls = SegmentActivities(probe.store)
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.environ["TEMPORAL_NAMESPACE"]
    )
    async with Worker(
        client,
        task_queue=AGENT_TASK_QUEUE,
        workflows=[
            ToolExecutionWorkflow,
            CapabilityInvocationWorkflow,
            DeterministicCapabilityWorkflow,
        ],
        activities=[
            probe.prepare,
            probe.observe,
            probe.close,
            probe.publish,
            controls.acquire,
            controls.release,
            controls.begin_wait,
            controls.finish_wait,
        ],
    ):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
