from __future__ import annotations

from uuid import UUID

from temporalio import activity

from web_artifacts.build import ArtifactBuildService
from web_artifacts.models import ArtifactBuildInput


class ArtifactActivities:
    def __init__(self, build_service: ArtifactBuildService) -> None:
        self._build_service = build_service

    @activity.defn(name="execute_artifact_build_activity")
    async def execute(self, request: ArtifactBuildInput) -> dict[str, str]:
        request.validate()
        return await self._build_service.execute(UUID(request.artifact_version_id))
