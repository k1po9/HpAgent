"""Approved persistent overwrite on the existing durable operation runtime."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    ApprovedToolExecutionInput,
    ApprovedToolExecutionResult,
)
from file_domain.persistent import DestinationChanged, PersistentWebFileService

from .side_effects import FaultInjector, NoopFaultInjector
from .store import AgentDataStore, StaleFencingToken


class PersistentOverwriteActivities:
    def __init__(
        self, store: AgentDataStore, persistent: PersistentWebFileService,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self.store = store
        self.persistent = persistent
        self.fault_injector = fault_injector or NoopFaultInjector()

    @activity.defn(name="approved_file_action_execution_activity")
    async def execute(
        self, request: ApprovedToolExecutionInput,
    ) -> ApprovedToolExecutionResult:
        if request.schema_version != AGENT_SCHEMA_VERSION:
            raise ApplicationError("unsupported schema", non_retryable=True)
        try:
            await asyncio.to_thread(
                self.store.validate_and_renew_lease, request.account_id,
                request.run_id, request.lease_token,
            )
            operation = await asyncio.to_thread(
                self.store.begin_tool_operation, request.operation_id, request.run_id
            )
            if operation.status == "completed":
                payload = operation.result_payload or {}
                return ApprovedToolExecutionResult(**payload)
            outcome, recovered = await asyncio.to_thread(
                self.persistent.reconcile_approved_overwrite,
                UUID(request.account_id), UUID(request.run_id), request.operation_id,
            )
            if operation.status == "started":
                await asyncio.to_thread(
                    self.store.record_operation_intent, request.operation_id,
                    {"schema_version": 1, "tool": "save_persistent_file",
                     "side_effect_class": "non_idempotent_write",
                     "approval_id": request.approval_id},
                )
            if outcome == "conflict":
                raise ApplicationError(
                    "persistent destination conflict", type="destination_conflict",
                    non_retryable=True,
                )
            if recovered is None:
                await asyncio.to_thread(
                    self.store.validate_and_renew_lease, request.account_id,
                    request.run_id, request.lease_token,
                )
                recovered = await asyncio.to_thread(
                    self.persistent.execute_approved_overwrite,
                    UUID(request.account_id), UUID(request.run_id), request.operation_id,
                    execution_id=request.operation_id,
                    fencing_token=request.lease_token,
                )
                self.fault_injector.hit("persistent_overwrite_succeeded_before_ack")
            result = ApprovedToolExecutionResult(
                1, request.operation_id, f"persistent-file:{recovered.file_id}",
                f"Saved {recovered.logical_path} revision {recovered.revision}",
                request.transcript_version,
            )
            await asyncio.to_thread(
                self.store.complete_operation_with_event,
                transcript_id=request.transcript_id,
                expected_version=request.transcript_version,
                event_type="tool_result", operation_id=request.operation_id,
                event_payload={"message": {"role": "tool",
                    "tool_call_id": request.tool_call_id, "name": request.tool_name,
                    "content": result.display_summary},
                    "side_effect_class": "non_idempotent_write"},
                result_ref=result.result_ref, result_payload=asdict(result),
            )
            completed = await asyncio.to_thread(
                self.store.begin_tool_operation, request.operation_id, request.run_id
            )
            return ApprovedToolExecutionResult(**(completed.result_payload or asdict(result)))
        except (StaleFencingToken, DestinationChanged) as exc:
            raise ApplicationError(
                str(exc), type="destination_conflict", non_retryable=True
            ) from exc
