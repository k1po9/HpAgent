"""Trusted capability discovery and short transactional Harness boundaries."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Callable
from uuid import UUID

from jsonschema.exceptions import ValidationError
from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_workflows.capability_contracts import (
    CAPABILITY_TOOL_PREFIX,
    CapabilityCall,
    CapabilityClose,
    CapabilityExecutionInput,
    CapabilityFinish,
    CapabilityPrepared,
)
from agent_workflows.contracts import AGENT_SCHEMA_VERSION, ToolExecutionResult
from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import CapabilityObservation, CapabilityRegistry
from run_domain.execution import (
    StaleFencingToken,
    TranscriptVersionConflict,
    assert_execution_fence,
    lock_execution,
)
from run_domain.execution_context import execution_fence
from run_domain.lifecycle import RunLifecycleService
from run_domain.results import ResultReceiptService, complete_operation
from workspace.resources import ResourceDenied

from .fencing import fenced_activity


def empty_scope(inputs):
    return dict(schema_version=1, node_ids=[], file_ids=[], tool_names=[], result_ids=[])


@dataclass(frozen=True)
class CapabilityBinding:
    key: str
    contract_version: int
    workflow_name: str
    scope: Callable[[dict[str, Any]], dict[str, Any]] = empty_scope

    @property
    def tool_name(self):
        # Include the contract version and use an injective encoding of key punctuation.
        encoded = self.key.replace("_", "_u").replace(".", "_d").replace("-", "_h")
        return f"{CAPABILITY_TOOL_PREFIX}{encoded}__v{self.contract_version}"


class CapabilityActivities:
    def __init__(self, store, facts: CapabilityFacts, bindings: tuple[CapabilityBinding, ...] = ()):
        self.store = store
        self.facts = facts
        self.bindings = {b.tool_name: b for b in bindings}
        if len(self.bindings) != len(bindings):
            raise ValueError("duplicate capability tool")
        for binding in bindings:
            definition = facts.registry.get(binding.key, binding.contract_version)
            if definition["execution_mode"] != "isolated_execution" or not binding.workflow_name:
                raise ValueError("capability binding requires an isolated Workflow")
            if len(binding.tool_name) > 64:
                raise ValueError("capability tool name exceeds provider limit")

    def discover(self, surface):
        available = {
            (d["key"], d["contract_version"]): d
            for d in self.facts.registry.discover(surface, set(self.facts.services))
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": d["description"],
                    "parameters": d["input_schema"],
                },
            }
            for name, b in self.bindings.items()
            if (d := available.get((b.key, b.contract_version))) is not None
        ]

    @activity.defn(name="prepare_capability_activity")
    @fenced_activity
    async def prepare(self, request: CapabilityCall) -> CapabilityPrepared:
        if request.tool.schema_version != AGENT_SCHEMA_VERSION or (
            request.account_id,
            request.run_id,
            request.execution_id,
            request.operation_id,
        ) != (
            request.tool.account_id,
            request.tool.run_id,
            request.tool.execution_id,
            request.tool.operation_id,
        ):
            raise ApplicationError("invalid capability call schema", non_retryable=True)
        prior = await asyncio.to_thread(
            self.store.begin_tool_operation, request.operation_id, request.run_id
        )
        if prior.status == "completed":
            return CapabilityPrepared(
                replay=ToolExecutionResult(
                    **{
                        k: v
                        for k, v in prior.result_payload.items()
                        if k in ToolExecutionResult.__dataclass_fields__
                    }
                )
            )
        if prior.status in {"uncertain", "intent_recorded"}:
            raise ApplicationError("capability caller has unresolved effect", non_retryable=True)
        try:
            return await asyncio.to_thread(self._prepare, request)
        except ValidationError:
            return CapabilityPrepared(error="capability input schema validation failed")
        except (ValueError, PermissionError, ResourceDenied) as exc:
            # An immutable identity conflict must never overwrite the in-flight invocation.
            with UnitOfWork(self.store.database_url) as uow:
                assert_execution_fence(uow)
                if uow.execute(
                    "SELECT 1 FROM capability_invocations WHERE invocation_id=%s",
                    (request.operation_id,),
                ).fetchone():
                    raise ApplicationError(
                        "capability invocation recovery conflict",
                        type="capability_identity_conflict",
                        non_retryable=True,
                    ) from exc
            return CapabilityPrepared(error=str(exc)[:500])

    @retryable_transaction
    def _prepare(self, request):
        binding = self.bindings.get(request.tool.tool_call.name)
        if binding is None:
            raise ResourceDenied("capability is not registered")
        inputs = self.store.tool_call_arguments(
            request.tool.tool_call.arguments_ref, request.tool.tool_call.tool_call_id
        )
        definition = self.facts.registry.get(binding.key, binding.contract_version)
        with UnitOfWork(self.store.database_url) as uow:
            run, parent = self.facts._context(uow)
            if str(parent["execution_id"]) != request.tool.execution_id:
                raise ResourceDenied("caller identity mismatch")
            # Serialize cross-process admission; no physical ticket survives this transaction.
            uow.execute("SELECT pg_advisory_xact_lock(4830020601)")
            prior = uow.execute(
                "SELECT 1 FROM capability_invocations WHERE invocation_id=%s",
                (request.operation_id,),
            ).fetchone()
            if not prior:
                counts = uow.execute(
                    "SELECT count(*) AS total,count(*) FILTER(WHERE account_id=%s) AS account,"
                    "count(*) FILTER(WHERE parent_execution_id=%s) AS parent FROM run_executions "
                    "WHERE role='capability' AND status IN ('queued','running','cancelling')",
                    (run["account_id"], parent["execution_id"]),
                ).fetchone()
                if (
                    counts["parent"]
                    or counts["total"] >= int(os.getenv("CAPABILITY_GLOBAL_EXECUTIONS", "32"))
                    or counts["account"] >= int(os.getenv("CAPABILITY_ACCOUNT_EXECUTIONS", "4"))
                ):
                    raise ResourceDenied("capability capacity exhausted")
                # Admission is only a headroom check. Implementations still reserve every real
                # model/tool call in the existing account/day + Work + Run ledger.
                if (
                    definition["limits"]["max_model_calls"]
                    or definition["limits"]["max_tool_calls"]
                ):
                    for table, column, value in (
                        ("run_budgets", "run_id", run["run_id"]),
                        ("work_budgets", "work_id", run["work_id"]),
                    ):
                        if value is None:
                            continue
                        budget = uow.execute(
                            f"SELECT limits,used,reserved FROM {table} WHERE {column}=%s", (value,)
                        ).fetchone()
                        dimensions = []
                        if definition["limits"]["max_model_calls"]:
                            dimensions.extend(
                                [
                                    "model_calls",
                                    "model_total_tokens",
                                    "model_input_tokens",
                                    "model_output_tokens",
                                ]
                            )
                        if definition["limits"]["max_tool_calls"]:
                            dimensions.append("tool_calls")
                        if not budget or any(
                            budget["limits"].get(d, 0)
                            - budget["used"].get(d, 0)
                            - budget["reserved"].get(d, 0)
                            <= 0
                            for d in dimensions
                        ):
                            raise ResourceDenied("aggregate invocation budget exhausted")
            invocation = self.facts.create(
                uow,
                operation_id=request.operation_id,
                capability_key=binding.key,
                contract_version=binding.contract_version,
                inputs=inputs,
                scope=binding.scope(inputs),
                invocation_limits=definition["limits"],
            )
            committed = uow.execute(
                "SELECT 1 FROM capability_results WHERE invocation_id=%s", (request.operation_id,)
            ).fetchone()
            child = CapabilityExecutionInput(
                request.account_id,
                request.run_id,
                str(invocation["child_execution_id"]),
                request.operation_id,
                binding.workflow_name,
                definition["limits"]["max_duration_seconds"],
                definition["retry_policy"]["max_attempts"],
                f"{request.operation_id}:produce",
            )
            return CapabilityPrepared(child=child, result_committed=bool(committed))

    def invocation_context(self, uow, request):
        """Trusted implementations load inputs under their own current segment fence."""
        run, execution = self.facts._context(uow)
        if execution["role"] != "capability" or execution["invocation_id"] != request.invocation_id:
            raise ResourceDenied("invocation is outside producer Execution")
        invocation = uow.execute(
            "SELECT * FROM capability_invocations WHERE account_id=%s AND invocation_id=%s",
            (run["account_id"], request.invocation_id),
        ).fetchone()
        descriptor = invocation["descriptor"]
        if (
            self.facts.registry.get(invocation["capability_key"], invocation["contract_version"])
            != descriptor["definition"]
        ):
            raise ResourceDenied("registered capability implementation changed")
        self._authorize_scope(uow, run, descriptor)
        return descriptor

    def _authorize_scope(self, uow, run, descriptor):
        from workspace.resources import ResourcePolicy

        policy = ResourcePolicy(uow)
        scope = descriptor["resource_scope"]
        for ref in scope["result_ids"]:
            self.facts.read_ref(uow, UUID(ref))
        for file in scope["file_ids"]:
            if not policy._file_authorized_in_uow(
                uow, run["account_id"], run["run_id"], UUID(file)
            ):
                raise ResourceDenied("capability file permission revoked")
        for node in scope["node_ids"]:
            snapshot = uow.execute(
                "SELECT * FROM run_resource_snapshots WHERE account_id=%s AND run_id=%s AND status='ready'",
                (run["account_id"], run["run_id"]),
            ).fetchone()
            if not snapshot or any(
                not policy._grants(
                    uow,
                    run["account_id"],
                    snapshot["subject_kind"],
                    snapshot["subject_id"],
                    UUID(node),
                    action,
                )
                for action in descriptor["definition"]["resource_operations"]
            ):
                raise ResourceDenied("capability node permission revoked")

    def commit_result(self, request, result, *, usage=None):
        """Commit a typed producer result and optional usage settlement atomically.

        The stage must already have registered its Operation and reserved real usage.
        A stale attempt retains only its own receipt; it never publishes under a new fence.
        """
        if usage is not None and any(dimension.startswith("model_") for dimension in usage):
            raise ValueError("model usage must settle through ModelBudgetCoordinator")
        try:
            return self._commit_result(request, result, usage)
        except StaleFencingToken:
            payload = result.to_dict()
            payload["provenance"] = dict(
                schema_version=1,
                invocation_id=request.invocation_id,
                execution_id=request.execution_id,
                operation_id=request.operation_id,
            )
            ResultReceiptService(self.store.database_url).receive(
                request.account_id,
                request.run_id,
                request.execution_id,
                request.operation_id,
                request.lease_token,
                payload,
            )
            raise

    @retryable_transaction
    def _commit_result(self, request, result, usage):
        with UnitOfWork(self.store.database_url) as uow:
            self.invocation_context(uow, request)
            self.facts.publish_result(
                uow,
                invocation_id=request.invocation_id,
                producing_operation_id=request.operation_id,
                result=result,
            )
            if usage is not None:
                from resources.run_budget import RunBudgetService

                RunBudgetService(self.store.database_url).settle_in_uow(
                    uow, UUID(request.run_id), request.operation_id, usage, "measured"
                )
        return f"capability-result:{request.invocation_id}"

    @activity.defn(name="observe_capability_activity")
    @fenced_activity
    async def observe(self, request: CapabilityFinish) -> ToolExecutionResult:
        return await asyncio.to_thread(self._observe, request)

    @retryable_transaction
    def _observe(self, request):
        tool = request.call.tool
        with UnitOfWork(self.store.database_url) as uow:
            assert_execution_fence(uow)
            self.store._owned_record(uow, "agent_transcripts", "transcript_id", tool.transcript_id)
            operation = uow.execute(
                "SELECT * FROM execution_operations WHERE operation_id=%s FOR UPDATE",
                (request.operation_id,),
            ).fetchone()
            self.store._owned_record(
                uow, "execution_operations", "operation_id", request.operation_id
            )
            if operation["status"] == "completed":
                return ToolExecutionResult(
                    **{
                        k: v
                        for k, v in operation["result_payload"].items()
                        if k in ToolExecutionResult.__dataclass_fields__
                    }
                )
            if not request.error:
                run, _ = self.facts._context(uow)
                invocation = uow.execute(
                    "SELECT descriptor FROM capability_invocations WHERE account_id=%s AND invocation_id=%s",
                    (run["account_id"], request.operation_id),
                ).fetchone()
                self._authorize_scope(uow, run, invocation["descriptor"])
            result = (
                self.facts.read_result(uow, request.operation_id)
                if not request.error
                else dict(outcome="failed", summary=request.error, error="admission_denied")
            )
            # uncertain is a control condition, never ordinary successful tool feedback.
            if result["outcome"] in {"uncertain", "cancelled"}:
                raise ApplicationError(
                    "capability requires reconciliation or cancellation",
                    type="capability_" + result["outcome"],
                    non_retryable=True,
                )
            transcript = uow.execute(
                "SELECT version FROM agent_transcripts WHERE transcript_id=%s FOR UPDATE",
                (tool.transcript_id,),
            ).fetchone()
            if transcript["version"] != tool.transcript_version:
                raise TranscriptVersionConflict("capability observation transcript changed")
            sequence = transcript["version"] + 1
            pointer = None
            if not request.error:
                pointer = CapabilityObservation(
                    request.operation_id,
                    result["provenance"]["operation_id"],
                    f"capability-result:{request.operation_id}",
                ).to_dict()
            reference = f"capability-observation:{request.operation_id}"
            response = ToolExecutionResult(
                AGENT_SCHEMA_VERSION,
                request.operation_id,
                reference,
                sequence,
                result["summary"],
                tool_success=result["outcome"] == "succeeded",
            )
            payload = asdict(response)
            event = {
                "message": {
                    "role": "tool",
                    "tool_call_id": tool.tool_call.tool_call_id,
                    "name": tool.tool_call.name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            }
            if pointer:
                payload["capability_observation"] = pointer
                event["capability_observation"] = pointer
            uow.execute(
                "INSERT INTO agent_transcript_events(transcript_id,sequence,event_type,operation_id,payload) "
                "VALUES (%s,%s,'tool_result',%s,%s)",
                (tool.transcript_id, sequence, request.operation_id, Jsonb(event)),
            )
            uow.execute(
                "UPDATE agent_transcripts SET version=%s,updated_at=now() WHERE transcript_id=%s",
                (sequence, tool.transcript_id),
            )
            fence = execution_fence.get()
            if not uow.execute(
                "SELECT 1 FROM execution_operation_attempts WHERE operation_id=%s AND fencing_token=%s",
                (request.operation_id, fence[3]),
            ).fetchone():
                uow.execute(
                    "UPDATE execution_operations SET attempt_count=attempt_count+1 WHERE operation_id=%s",
                    (request.operation_id,),
                )
            ResultReceiptService.register_attempt(uow, request.operation_id, fence)
            complete_operation(uow, request.operation_id, reference, payload)
            if pointer:
                self.facts.record_observation(
                    uow,
                    invocation_id=request.operation_id,
                    producing_operation_id=pointer["producing_operation_id"],
                    transcript_id=tool.transcript_id,
                    sequence=sequence,
                )
            return response

    @activity.defn(name="close_capability_activity")
    async def close(self, request: CapabilityClose) -> None:
        await asyncio.to_thread(self._close, request)

    @retryable_transaction
    def _close(self, request):
        child = request.child
        with UnitOfWork(self.store.database_url) as uow:
            run = RunLifecycleService.lock(uow, UUID(child.account_id), UUID(child.run_id))
            row = lock_execution(uow, child.account_id, child.run_id, child.execution_id)
            if row["role"] != "capability" or row["invocation_id"] != child.invocation_id:
                raise ValueError("invalid capability closure")
            if row["status"] in {"succeeded", "failed", "cancelled"}:
                return
            status = request.status
            if run["status"] not in {"queued", "running"} or row["status"] == "cancelling":
                status = "cancelled"
            if (
                status == "succeeded"
                and not uow.execute(
                    "SELECT 1 FROM capability_results WHERE invocation_id=%s",
                    (child.invocation_id,),
                ).fetchone()
            ):
                raise ValueError("Workflow return is not a committed capability result")
            uow.execute(
                "UPDATE run_executions SET status=%s,updated_at=now() WHERE execution_id=%s",
                (status, row["execution_id"]),
            )
            uow.execute(
                "UPDATE execution_attempt_leases SET lease_expires_at=NULL,owner_segment_id=NULL,"
                "fencing_token=fencing_token+1 WHERE execution_id=%s",
                (row["execution_id"],),
            )


def empty_capabilities(store):
    return CapabilityActivities(store, CapabilityFacts(CapabilityRegistry()))
