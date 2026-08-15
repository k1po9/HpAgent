from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from web_artifacts.models import ArtifactBuildInput

from .web_workflow import WEB_LIFECYCLE_TASK_QUEUE

# Artifact has its own Workflow type and domain lifecycle, but deliberately
# shares the Web lifecycle Worker queue.  Keep this as an alias (not another
# string) so Dispatcher, Workflow and Worker registration cannot drift.
ARTIFACT_TASK_QUEUE = WEB_LIFECYCLE_TASK_QUEUE


@workflow.defn
class ArtifactBuildWorkflow:
    @workflow.run
    async def run(self, request: ArtifactBuildInput) -> dict[str, str | int]:
        request.validate()
        result = await workflow.execute_activity(
            "execute_artifact_build_activity", request,
            task_queue=ARTIFACT_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return {"schema_version": 1, "artifact_version_id": request.artifact_version_id,
                "status": result["status"]}
