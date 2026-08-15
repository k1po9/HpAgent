from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactBuildInput:
    schema_version: int
    artifact_version_id: str

    def validate(self) -> None:
        if self.schema_version != 1 or not self.artifact_version_id:
            raise ValueError("invalid ArtifactBuildInput")
