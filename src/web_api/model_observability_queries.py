"""Web query boundary for governed model-input visibility."""
from __future__ import annotations

from uuid import UUID

from account.entitlement_service import EntitlementService, EntitlementState
from model_observability.snapshot_projection import (
    full_safe_projection,
    minimal_projection,
    summary_projection,
)
from model_observability.snapshot_repository import SnapshotQueryRepository
from web_domain.errors import ResourceNotFound


class ModelInputUnavailable(RuntimeError):
    pass


class ModelObservabilityQueries:
    def __init__(self, database: object):
        self._repository = SnapshotQueryRepository(database)
        self._entitlements = EntitlementService(database)

    def _visibility(self, account_id: UUID) -> str:
        lookup = self._entitlements.get(account_id)
        if lookup.state is not EntitlementState.VALID or lookup.entitlement is None:
            return "none"
        return lookup.entitlement.prompt_visibility

    def list_run_model_snapshots(self, account_id: UUID, run_id: UUID) -> dict[str, object]:
        rows = self._repository.list_for_run(account_id, run_id)
        if rows is None:
            raise ResourceNotFound()
        visibility = self._visibility(account_id)
        project = summary_projection if visibility in {"summary", "full_safe"} else minimal_projection
        return {"visibility": visibility, "items": [project(row) for row in rows]}

    def get_model_snapshot(self, account_id: UUID, snapshot_id: UUID) -> dict[str, object]:
        row = self._repository.get(account_id, snapshot_id)
        if row is None:
            raise ResourceNotFound()
        visibility = self._visibility(account_id)
        if visibility == "none":
            raise ModelInputUnavailable()
        projection = summary_projection(row)
        if visibility == "full_safe":
            projection = full_safe_projection(row)
        return {"visibility": visibility, "model_input": projection}
