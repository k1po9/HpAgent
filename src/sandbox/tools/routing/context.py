from pathlib import Path
from typing import Any, Callable

from .models import ResourceFact, ResourceScope, RuntimeCapabilitySnapshot


class ToolSelectionContextBuilder:
    def __init__(self, *, surface: str, workspace_path: str, run_scope_provider: Callable[[], Any | None], available_services: frozenset[str] = frozenset()):
        self._surface = surface
        self._workspace_path = workspace_path
        self._run_scope_provider = run_scope_provider
        self._available_services = available_services

    def snapshot(self) -> RuntimeCapabilitySnapshot:
        scope = self._run_scope_provider()
        resources = () if scope is None else tuple(
            ResourceFact(
                resource_id=str(item.file_id), logical_name=item.logical_name,
                scope=ResourceScope.CURRENT_RUN, media_type=item.content_type,
                extension=Path(item.logical_name).suffix.lower().lstrip(".") or None,
                direction=item.direction,
            ) for item in (*scope.inputs, *scope.outputs)
        )
        return RuntimeCapabilitySnapshot(
            surface=self._surface,
            has_workspace=bool(self._workspace_path),
            has_run_file_scope=scope is not None,
            resources=resources,
            available_services=self._available_services,
        )
