from __future__ import annotations

import asyncio
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from web_artifacts.build import ArtifactBuildService
from web_artifacts.generator import ArtifactGenerationError


class ArtifactActivities:
    def __init__(self, build_service: ArtifactBuildService) -> None:
        self._build_service = build_service

    @activity.defn(name="execute_artifact_build_activity")
    async def execute(self, request):
        from orchestration.run_lifecycle_contracts import RunLifecycleInput
        from persistence.uow import UnitOfWork

        request = RunLifecycleInput(**request) if isinstance(request, dict) else request
        request.validate()
        with UnitOfWork(self._build_service.database) as uow:
            row = uow.execute(
                "SELECT input_snapshot FROM runs WHERE run_id=%s", (UUID(request.run_id),)
            ).fetchone()
        version_id = UUID(row["input_snapshot"]["requirement"]["spec"]["artifact_version_id"])

        async def heartbeat():
            while True:
                activity.heartbeat({"run_id": request.run_id})
                await asyncio.sleep(5)

        pulse = asyncio.create_task(heartbeat())
        try:
            return await self._build_service.execute(
                version_id,
                run_id=UUID(request.run_id),
                workflow_id=activity.info().workflow_id,
                attempt=activity.info().attempt,
            )
        except ArtifactGenerationError as exc:
            raise ApplicationError(
                exc.safe_message, type=exc.code, non_retryable=not exc.retryable
            ) from exc
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
