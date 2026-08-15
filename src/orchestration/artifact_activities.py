from __future__ import annotations

from uuid import UUID

from temporalio import activity

from web_artifacts.build import ArtifactBuildService
from web_artifacts.models import ArtifactBuildInput

_build_service: ArtifactBuildService | None = None


def inject_artifact_build_service(service: ArtifactBuildService) -> None:
    global _build_service
    _build_service = service


@activity.defn
async def execute_artifact_build_activity(request: ArtifactBuildInput) -> dict[str, str]:
    request.validate()
    if _build_service is None:
        raise RuntimeError("ArtifactBuildService was not injected")
    return await _build_service.execute(UUID(request.artifact_version_id))
