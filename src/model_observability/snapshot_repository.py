"""Ownership-scoped reads for immutable model input snapshots."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from persistence.uow import UnitOfWork


class SnapshotQueryRepository:
    def __init__(self, database: object):
        self._database = database

    def list_for_run(self, account_id: UUID, run_id: UUID) -> Sequence[Mapping[str, Any]] | None:
        """Return snapshots only when the same Account owns the Run."""
        with UnitOfWork(self._database) as uow:
            owned_run = uow.execute(
                "SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s",
                (account_id, run_id),
            ).fetchone()
            if owned_run is None:
                return None
            return uow.execute(
                "SELECT s.* FROM model_input_snapshots s JOIN runs r "
                "ON r.account_id=s.account_id AND r.run_id=s.run_id "
                "WHERE s.account_id=%s AND s.run_id=%s "
                "ORDER BY s.created_at,s.fallback_attempt,s.snapshot_id",
                (account_id, run_id),
            ).fetchall()

    def get(self, account_id: UUID, snapshot_id: UUID) -> Mapping[str, Any] | None:
        with UnitOfWork(self._database) as uow:
            return uow.execute(
                "SELECT s.* FROM model_input_snapshots s JOIN runs r "
                "ON r.account_id=s.account_id AND r.run_id=s.run_id "
                "WHERE s.account_id=%s AND s.snapshot_id=%s",
                (account_id, snapshot_id),
            ).fetchone()
