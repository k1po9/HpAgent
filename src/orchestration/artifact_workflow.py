from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from web_artifacts.models import ArtifactBuildInput


ARTIFACT_TASK_QUEUE = "hpagent-web-lifecycle-v1"


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
