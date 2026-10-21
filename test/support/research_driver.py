"""Direct-Activity integration driver for the fixed parent + local capability boundary.

No second implementation: every stage invokes the same production Activities.
"""

from contextlib import contextmanager
from dataclasses import replace
from tempfile import TemporaryDirectory
from uuid import uuid4

from agent_workflows.capability_contracts import CapabilityClose
from agent_workflows.lifecycle_contracts import LIFECYCLE_SCHEMA_VERSION, SegmentInput
from file_runtime import OutputPublisher, ResearchMarkdownPublisher
from orchestration.research_workflow import ResearchWorkflowInput
from persistence.uow import UnitOfWork
from research_activities import ResearchActivities
from research_activities.composition import compose_research
from research_domain.context import research_request
from run_domain.execution_context import fence_scope
from storage.tenant_file_store import TenantFileStore


class ResearchDriver:
    def __init__(self, *args, **kwargs):
        if not kwargs.get("markdown_publisher"):
            self.directory = TemporaryDirectory(prefix="hpagent-s3-research-test-")
            kwargs["markdown_publisher"] = ResearchMarkdownPublisher(
                OutputPublisher(
                    args[0], TenantFileStore(self.directory.name, max_bytes=1024 * 1024)
                )
            )
        self.research = ResearchActivities(*args, **kwargs)
        self.capabilities, self.parent = compose_research(self.research)
        self.children = {}

    async def prepare_research_activity(self, request):
        child = await self.parent.prepare_research_parent_activity(request)
        self.children[request.run_id] = child
        return await self.research.prepare_research_activity(
            ResearchWorkflowInput(
                1, child.run_id, child.invocation_id, child.account_id, child.execution_id
            )
        )

    def local(self, request):
        child = self.children[request.run_id]
        return replace(
            request,
            invocation_id=child.invocation_id,
            account_id=child.account_id,
            execution_id=child.execution_id,
        )

    def parent_request(self, request):
        return ResearchWorkflowInput(1, request.run_id, self.children[request.run_id].invocation_id)

    async def publish_research_artifact_activity(self, request):
        await self.research.commit_research_result_activity(self.local(request))
        return await self.parent.publish_research_artifact_activity(self.parent_request(request))

    async def save_research_workspace_activity(self, request):
        return await self.parent.save_research_workspace_activity(self.parent_request(request))

    async def complete_research_activity(self, request):
        await self.capabilities.close(CapabilityClose(self.children[request.run_id], "succeeded"))
        return await self.parent.complete_research_activity(self.parent_request(request))

    @contextmanager
    def parent_scope(self, run_id):
        request = self.parent_request(ResearchWorkflowInput(1, str(run_id)))
        identity = self.research.store.run_identity(str(run_id))
        segment = SegmentInput(
            LIFECYCLE_SCHEMA_VERSION,
            str(run_id),
            identity["account_id"],
            str(uuid4()),
            execution_id=identity["execution_id"],
        )
        token = self.research.store.acquire_segment(segment)
        reset = research_request.set(request)
        try:
            with fence_scope(identity["account_id"], str(run_id), token, identity["execution_id"]):
                yield
        finally:
            research_request.reset(reset)
            self.research.store.release_segment(segment)

    def _save_workspace(self, run_id):
        with UnitOfWork(self.research.database) as uow:
            intent = uow.execute(
                "SELECT state FROM research_run_save_intents WHERE run_id=%s", (run_id,)
            ).fetchone()
        if intent and intent["state"] == "succeeded":
            return self.parent._save_workspace(run_id)
        with self.parent_scope(run_id):
            return self.parent._save_workspace(run_id)

    def _complete(self, run_id):
        with self.parent_scope(run_id):
            return self.parent._complete(run_id)

    def _publish(self, run_id):
        # Read-only replay after a successfully completed fixed parent.
        with UnitOfWork(self.research.database) as uow:
            row = uow.execute(
                "SELECT artifact_id FROM research_reports WHERE run_id=%s AND invocation_id=%s",
                (run_id, self.children[str(run_id)].invocation_id),
            ).fetchone()
        if row and row["artifact_id"]:
            return 1
        with self.parent_scope(run_id):
            return self.parent._publish(run_id)

    def __getattr__(self, name):
        target = getattr(self.research, name)
        if name.endswith("_activity") and callable(target):

            async def stage(request):
                return await target(self.local(request))

            return stage
        return target
