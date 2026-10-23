"""Dedicated SIGKILL fixture around production text publication and observation."""

import asyncio
import os
from pathlib import Path

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from agent_activities.composition import compose_capabilities
from agent_activities.segments import SegmentActivities
from agent_activities.store import AgentDataStore
from agent_activities.text_result import TextActivities
from agent_workflows.capability import CapabilityInvocationWorkflow
from agent_workflows.capability_contracts import CapabilityFinish
from agent_workflows.contracts import AGENT_TASK_QUEUE
from agent_workflows.text import TextCapabilityWorkflow
from agent_workflows.tool_execution import ToolExecutionWorkflow
from file_runtime.output import OutputPublisher
from storage.tenant_file_store import TenantFileStore


async def main():
    database = os.environ['WORKER_DATABASE_URL']
    caps = compose_capabilities(AgentDataStore(database, lease_ttl_seconds=15), services=frozenset({'text'}))
    text = TextActivities(caps, OutputPublisher(database, TenantFileStore(
        Path(os.environ['HPAGENT_TEXT_TEST_STORE']), max_bytes=1024 * 1024)))
    segments = SegmentActivities(caps.store)
    marker = Path(os.environ['HPAGENT_TEXT_TEST_MARKER'])

    @activity.defn(name='observe_capability_activity')
    async def observe(request: CapabilityFinish):
        if not marker.exists():
            marker.write_text(request.call.operation_id)
            # Child result is committed; simulate interruption before parent receipt.
            while True:
                activity.heartbeat()
                await asyncio.sleep(0.1)
        return await caps.observe(request)

    client = await Client.connect(os.environ['TEMPORAL_HOST'], namespace=os.environ['TEMPORAL_NAMESPACE'])
    async with Worker(client, task_queue=AGENT_TASK_QUEUE,
        workflows=[ToolExecutionWorkflow, CapabilityInvocationWorkflow, TextCapabilityWorkflow],
        activities=[caps.prepare, observe, caps.close, text.produce,
            segments.acquire, segments.release, segments.begin_wait, segments.finish_wait]):
        await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
