"""Real isolated Temporal retries an HTML response lost after committing the result."""

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from agent_activities.segments import SegmentActivities
from agent_workflows.capability_contracts import CapabilityExecutionInput
from agent_workflows.contracts import AGENT_TASK_QUEUE
from agent_workflows.html import HtmlCapabilityWorkflow
from persistence.uow import UnitOfWork
from web_artifacts.local import HtmlActivities

pytestmark = [pytest.mark.postgres, pytest.mark.temporal, pytest.mark.asyncio]


async def test_html_committed_result_survives_lost_activity_reply(
    urls, owner, account_id, tmp_path
):
    from test_capability_facts import chat
    from test_html_capability import admit, setup

    run = chat(urls, account_id)
    caps, html, model = setup(urls, tmp_path)
    child = admit(caps, urls, account_id, run, {"text": "# Report"})

    class LostReply(HtmlActivities):
        lost = False

        @activity.defn(name="produce_html_activity")
        async def produce(self, request: CapabilityExecutionInput) -> str:
            result = await super().produce(request)
            if not self.lost:
                self.lost = True
                raise RuntimeError("simulated lost response after database commit")
            return result

    producer = LostReply(caps, html.generator, html.publisher)
    segments = SegmentActivities(caps.store)
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.environ["TEMPORAL_TEST_NAMESPACE"]
    )
    async with Worker(
        client,
        task_queue=AGENT_TASK_QUEUE,
        workflows=[HtmlCapabilityWorkflow],
        activities=[producer.produce, segments.acquire, segments.release],
    ):
        result = await client.execute_workflow(
            HtmlCapabilityWorkflow.run,
            replace(child, max_attempts=2),
            id=f"s4-html-{uuid4()}",
            task_queue=AGENT_TASK_QUEUE,
        )
    assert result == f"capability-result:{child.invocation_id}"
    assert model.calls == 1
    with UnitOfWork(urls[2]) as uow:
        assert (
            uow.execute(
                "SELECT count(*) AS n FROM capability_results WHERE invocation_id=%s",
                (child.invocation_id,),
            ).fetchone()["n"]
            == 1
        )
        assert (
            uow.execute(
                "SELECT count(*) AS n FROM agent_execution_segments WHERE execution_id=%s AND state='running'",
                (child.execution_id,),
            ).fetchone()["n"]
            == 0
        )


async def test_artifact_api_runs_production_parent_and_releases_root_while_generating(
    urls, owner, account_id, tmp_path
):
    from uuid import UUID

    from test_capability_facts import chat
    from test_html_capability import setup

    from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
    from orchestration.artifact_activities import ArtifactActivities
    from orchestration.run_dispatcher import RunStrategyActivities
    from orchestration.run_lifecycle_activities import RunLifecycleActivities
    from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE, RunLifecycleInput
    from run_domain.lifecycle import RunLifecycleService
    from web_artifacts.build import ArtifactBuildService
    from web_artifacts.services import ArtifactService
    from web_domain.lifecycle import WebRunLifecycleService

    source = chat(urls, account_id)
    RunLifecycleService(urls[2]).finish(account_id, source, "succeeded", content="# Source")
    message = owner.execute(
        "SELECT message_id FROM messages WHERE produced_by_run_id=%s", (source,)
    ).fetchone()["message_id"]
    created = ArtifactService(urls[1]).create_artifact(account_id, message, str(uuid4()))
    run = owner.execute(
        "SELECT * FROM runs WHERE account_id=%s AND executor_key='artifact_html'", (account_id,)
    ).fetchone()
    from web_domain.workflow_execution import PostgresWorkflowExecutionStore

    PostgresWorkflowExecutionStore(urls[2]).prepare_start(run["run_id"])
    caps, html, model = setup(urls, tmp_path)
    original = model.generate

    async def inspect_parent(**kwargs):
        with UnitOfWork(urls[2]) as uow:
            assert not uow.execute(
                "SELECT 1 FROM execution_attempt_leases l JOIN run_executions e USING(execution_id) WHERE e.run_id=%s AND e.role='root' AND l.lease_expires_at>clock_timestamp()",
                (run["run_id"],),
            ).fetchone()
        return await original(**kwargs)

    model.generate = inspect_parent
    build = ArtifactBuildService(urls[2], html.generator, caps, html.publisher)
    parent = ArtifactActivities(build)
    lifecycle = RunLifecycleActivities(WebRunLifecycleService(urls[2]), None)
    segments = SegmentActivities(caps.store)
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.environ["TEMPORAL_TEST_NAMESPACE"]
    )
    async with (
        Worker(
            client,
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
            workflows=[AgentLifecycleWorkflow],
            activities=[
                lifecycle.prepare_run,
                lifecycle.finalize_failed,
                lifecycle.finalize_cancelled,
                RunStrategyActivities(urls[2]).load_strategy,
                parent.prepare,
                parent.complete,
            ],
        ),
        Worker(
            client,
            task_queue=AGENT_TASK_QUEUE,
            workflows=[HtmlCapabilityWorkflow],
            activities=[html.produce, caps.close, segments.acquire, segments.release],
        ),
    ):
        import asyncio

        handle = await client.start_workflow(
            AgentLifecycleWorkflow.run,
            RunLifecycleInput(1, str(run["run_id"])),
            id=run["workflow_id"],
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        )
        try:
            await asyncio.wait_for(handle.result(), 45)
        finally:
            if (
                owner.execute(
                    "SELECT status FROM runs WHERE run_id=%s", (run["run_id"],)
                ).fetchone()["status"]
                != "succeeded"
            ):
                await handle.cancel()
    version = ArtifactService(urls[1]).get_version(
        account_id, UUID(created.body["version"]["artifact_version_id"])
    )["version"]
    assert version["status"] == "completed"
    assert model.calls == 1
    assert (
        owner.execute(
            "SELECT count(*) AS n FROM works WHERE account_id=%s", (account_id,)
        ).fetchone()["n"]
        == 0
    )
