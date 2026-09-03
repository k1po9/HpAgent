"""Idempotent Docling normalization on the dedicated Temporal task queue."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from temporalio import activity

from agent_execution.run_budget import RunBudgetService
from agent_execution.tracing.repository import PostgresTraceRepository
from file_domain.persistence import NormalizedDocumentRepository
from file_domain.serialization import normalized_document_to_dict
from file_runtime import FileResourceResolver
from persistence.uow import UnitOfWork, retryable_transaction

from .contracts import NormalizedDocumentRef, NormalizeDocumentInput


class DocumentActivities:
    def __init__(self, database: object, workspace: Any, provider: Any) -> None:
        self.database = database
        self.workspace = workspace
        self.provider = provider
        self.repository = NormalizedDocumentRepository()
        self.budget = RunBudgetService(database)
        self.trace = PostgresTraceRepository(database)

    @staticmethod
    def _result(row: dict[str, Any]) -> NormalizedDocumentRef:
        return {
            "schema_version": 1,
            "run_id": str(row["run_id"]),
            "file_id": str(row["file_id"]),
            "document_ref": str(row["document_ref"]),
            "block_count": int(row["block_count"]),
            "table_count": int(row["table_count"]),
            "truncated": bool(row["truncated"]),
        }

    @retryable_transaction
    def _find(self, operation_id: str) -> dict[str, Any] | None:
        with UnitOfWork(self.database) as uow:
            return self.repository.find_by_operation(uow, operation_id)

    @retryable_transaction
    def _persist(
        self, request: NormalizeDocumentInput, document_ref: str,
        document: dict[str, Any], block_count: int, table_count: int,
        truncated: bool,
    ) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            return self.repository.insert(
                uow,
                document_ref=document_ref,
                operation_id=request.operation_id,
                account_id=UUID(request.account_id),
                run_id=UUID(request.run_id),
                file_id=UUID(request.file_id),
                document=document,
                block_count=block_count,
                table_count=table_count,
                truncated=truncated,
            )

    def _normalize(self, request: NormalizeDocumentInput) -> tuple[dict[str, Any], int]:
        account_id, run_id, file_id = map(
            UUID, (request.account_id, request.run_id, request.file_id)
        )
        with self.workspace.prepare(account_id, run_id) as scope:
            resource = FileResourceResolver(scope).resolve(file_id)
            document = self.provider.parse(resource)
            payload = normalized_document_to_dict(document)
            scanned_bytes = resource.size_bytes
        return payload, scanned_bytes

    def _input_size(self, request: NormalizeDocumentInput) -> int:
        rows = self.workspace.load_rows(UUID(request.account_id), UUID(request.run_id))
        file_id = UUID(request.file_id)
        row = next((item for item in rows if item["file_id"] == file_id), None)
        if row is None:
            raise LookupError("file is not an input of the active Run")
        return int(row["size_bytes"])

    @retryable_transaction
    def _trace_parent(self, run_id: UUID) -> UUID | None:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT e.trace_event_id FROM trace_events e JOIN trace_runs r "
                "ON r.trace_run_id=e.trace_run_id WHERE r.run_id=%s "
                "AND e.parent_event_id IS NULL ORDER BY e.started_at LIMIT 1",
                (run_id,),
            ).fetchone()
        return row["trace_event_id"] if row is not None else None

    @retryable_transaction
    def _ledger_state(self, run_id: UUID, operation_id: str) -> str | None:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT state FROM run_usage_ledger WHERE run_id=%s AND operation_id=%s "
                "LIMIT 1",
                (run_id, operation_id),
            ).fetchone()
        return str(row["state"]) if row is not None else None

    async def _settle_replayed_budget(
        self, run_id: UUID, operation_id: str, scanned_bytes: int
    ) -> None:
        operations = (
            (f"{operation_id}:bytes", {"bytes_scanned": scanned_bytes}),
            (f"{operation_id}:wall", {"wall_time_ms": 1}),
        )
        for ledger_operation, actual in operations:
            state = await asyncio.to_thread(
                self._ledger_state, run_id, ledger_operation
            )
            if state == "reserved":
                await asyncio.to_thread(
                    self.budget.settle, run_id, ledger_operation, actual, "estimated"
                )

    @activity.defn(name="normalize_document_activity")
    async def normalize_document(
        self, request: NormalizeDocumentInput
    ) -> NormalizedDocumentRef:
        if request.schema_version != 1 or not request.operation_id.strip():
            raise ValueError("unsupported document Activity request")
        UUID(request.account_id), UUID(request.run_id), UUID(request.file_id)
        existing = await asyncio.to_thread(self._find, request.operation_id)
        if existing is not None:
            await self._settle_replayed_budget(
                UUID(request.run_id), request.operation_id,
                int(existing.get("scanned_bytes", 0)),
            )
            return self._result(existing)

        run_id = UUID(request.run_id)
        event_id = uuid5(NAMESPACE_URL, f"hpagent:document:{request.operation_id}")
        parent_event_id = await asyncio.to_thread(self._trace_parent, run_id)
        await asyncio.to_thread(
            self.trace.start_event, run_id, event_id, parent_event_id,
            "DocumentNormalization", "document_activity",
            {"operation_id": request.operation_id, "file_id": request.file_id},
        )
        started = time.monotonic()
        bytes_operation = f"{request.operation_id}:bytes"
        wall_operation = f"{request.operation_id}:wall"
        declared_size = 0
        try:
            declared_size = await asyncio.to_thread(self._input_size, request)
            await asyncio.to_thread(
                self.budget.reserve, run_id, bytes_operation,
                {"bytes_scanned": declared_size},
            )
            await asyncio.to_thread(
                self.budget.reserve, run_id, wall_operation, {"wall_time_ms": 600_000}
            )
            payload, scanned_bytes = await asyncio.to_thread(self._normalize, request)
            metadata = payload.get("metadata", {})
            row = await asyncio.to_thread(
                self._persist,
                request,
                f"normalized-document:{request.run_id}:{request.file_id}",
                payload,
                len(payload.get("blocks", [])),
                len(payload.get("tables", [])),
                bool(metadata.get("truncated", False)),
            )
            await asyncio.to_thread(
                self.budget.settle, run_id, bytes_operation,
                {"bytes_scanned": scanned_bytes}, "measured",
            )
            elapsed_ms = max(1, int((time.monotonic() - started) * 1000))
            await asyncio.to_thread(
                self.budget.settle, run_id, wall_operation,
                {"wall_time_ms": elapsed_ms}, "measured",
            )
            compact = self._result(row)
            await asyncio.to_thread(
                self.trace.finish_event, run_id, event_id, "completed", compact
            )
            return compact
        except Exception:
            elapsed_ms = max(1, int((time.monotonic() - started) * 1000))
            for ledger_operation, actual in (
                (bytes_operation, {"bytes_scanned": declared_size}),
                (wall_operation, {"wall_time_ms": elapsed_ms}),
            ):
                state = await asyncio.to_thread(
                    self._ledger_state, run_id, ledger_operation
                )
                if state == "reserved":
                    await asyncio.to_thread(
                        self.budget.settle,
                        run_id,
                        ledger_operation,
                        actual,
                        "estimated",
                    )
            await asyncio.to_thread(
                self.trace.finish_event, run_id, event_id, "failed",
                {"operation_id": request.operation_id},
            )
            raise
