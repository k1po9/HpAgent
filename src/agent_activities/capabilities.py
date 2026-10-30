"""Trusted capability discovery and short transactional Harness boundaries."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Callable
from uuid import UUID

from jsonschema import Draft202012Validator
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
from application.tool_policy import ToolPolicy, agent_role, tool_policy
from common.runtime_config import CapacityConfig
from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import (
    CapabilityObservation,
    CapabilityRegistry,
    normalize_inputs,
    resource_scope,
)
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


def input_validation_error(error: ValidationError) -> str:
    """Explain the trusted schema rule without echoing supplied values."""
    path = '/' + '/'.join(str(item) for item in error.absolute_schema_path)
    detail = f"capability input schema validation failed at schema {path[:160]}: {error.validator}"
    schema = error.schema if isinstance(error.schema, dict) else {}
    if error.validator in {'maxLength', 'minLength', 'type', 'required', 'format'}:
        detail += ' expected ' + json.dumps(error.validator_value, ensure_ascii=False)
    if schema.get('properties'):
        detail += '; allowed fields: ' + ', '.join(schema['properties'])
    if error.validator == 'oneOf':
        choices = [item.get('required', []) for item in schema.get('oneOf', [])]
        detail += '; choose exactly one input form: ' + json.dumps(choices)
        if isinstance(error.instance, dict):
            for form in schema.get('oneOf', []):
                kind = form.get('properties', {}).get('kind', {}).get('const')
                if kind is None or error.instance.get('kind') != kind:
                    continue
                missing = [name for name in form.get('required', []) if name not in error.instance]
                forbidden = [name for rule in form.get('not', {}).get('anyOf', [])
                             for name in rule.get('required', []) if name in error.instance]
                detail += '; ' + kind + ' missing fields: ' + ', '.join(missing)
                detail += '; set inapplicable fields to null: ' + ', '.join(forbidden)
                break
    return detail[:700]


def parent_input_schema(definition, *, model_facing=False):
    """The model-facing HTML tool requires an explicit delegated objective.

    Fixed/programmatic v1 callers keep their optional instruction contract.
    Parent agents must carry the requested behavior across the isolated boundary.
    """
    schema = deepcopy(definition['input_schema'])
    if definition['key'] == 'html':
        schema['required'] = list(dict.fromkeys([*schema.get('required', []), 'instruction']))
        schema['properties']['instruction']['minLength'] = 1
        if model_facing:
            schema.pop('oneOf', None)
            schema['required'] = list(schema['properties'])
            for name in ('text', 'source_ref', 'previous_ref'):
                schema['properties'][name]['type'] = ['string', 'null']
    return schema


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
    def __init__(self, store, facts: CapabilityFacts, bindings: tuple[CapabilityBinding, ...] = (), *, policy: ToolPolicy | None = None):
        self.store = store
        self.facts = facts
        self.tool_policy = policy if policy is not None else tool_policy()
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
        if surface not in {"chat", "work"}:
            return []
        available = {
            (d["key"], d["contract_version"]): d
            for d in self.facts.registry.discover(surface, set(self.facts.services))
        }
        from agent_activities.result_tools import discover
        manifests = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": d["description"] + (" Include every declared input field, using null for text/source_ref/previous_ref when not applicable." if b.key == "html" else ""),
                    "parameters": parent_input_schema(d, model_facing=True),
                },
            }
            for name, b in self.bindings.items()
            if (d := available.get((b.key, b.contract_version))) is not None
        ] + (discover() if available else [])
        role = agent_role(surface)
        return [tool for tool in manifests if self.tool_policy.allows(role, tool["function"]["name"])]

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
            reason = await asyncio.to_thread(self.protection, request.account_id, request.run_id)
            if reason:
                raise ApplicationError(reason, type=reason, non_retryable=True)
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
        except ValidationError as exc:
            return CapabilityPrepared(error=input_validation_error(exc))
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
            existing = uow.execute("SELECT * FROM capability_invocations WHERE invocation_id=%s", (request.operation_id,)).fetchone()
            if existing:
                definition = existing['descriptor']['definition']
            inputs = normalize_inputs(inputs, definition['input_schema'])
            if not existing:
                self.tool_policy.require(agent_role(run["source_kind"], parent["role"]), request.tool.tool_call.name)
                Draft202012Validator(parent_input_schema(definition)).validate(inputs)
                targets = (run.get('input_snapshot') or {}).get('requirement', {}).get('deliverables')
                if binding.key == 'text' and targets:
                    formats = {t['format'] for t in targets['items']}
                    if inputs.get('format', 'markdown') not in formats:
                        raise ValueError('output format does not match Requirement deliverables')
            if not existing and binding.key == "research":
                budget = uow.execute("SELECT limits,used,reserved FROM run_budgets WHERE run_id=%s", (run["run_id"],)).fetchone()
                used = budget["used"].get("research_iterations", 0)
                limit = budget["limits"].get("research_iterations", 0)
                remaining = max(0, limit - used - budget["reserved"].get("research_iterations", 0))
                if not remaining:
                    queries = uow.execute("SELECT DISTINCT query FROM research_iterations WHERE run_id=%s LIMIT 12", (run["run_id"],)).fetchall()
                    raise ValueError(f"Research stopped: this Run used {used}/{limit} research rounds; remaining=0. No search started. Do not call Research again or create a Run to bypass the limit. Queries: " + json.dumps([row["query"] for row in queries], ensure_ascii=False)[:250])
                from research_adapters.web import discovery_query
                from research_domain.models import SourceStrategy
                strategy = SourceStrategy.from_dict(inputs.get("source_strategy"))
                query = discovery_query(inputs["objective"], strategy)
                if uow.execute(
                    "SELECT 1 FROM research_iterations i JOIN research_plans p USING(research_id) "
                    "WHERE i.run_id=%s AND i.query=%s AND p.plan->'source_strategy'=%s::jsonb LIMIT 1",
                    (run["run_id"], query, json.dumps(strategy.to_dict())),
                ).fetchone():
                    raise ValueError("Research stopped: no new effective query or source strategy. Reuse the prior result and report its gaps; do not repeat Research in this Run.")
            self.admit_in_uow(uow, run, parent, definition, request.operation_id)
            if existing:
                if (existing['account_id'] != run['account_id'] or existing['run_id'] != run['run_id']
                        or existing['caller_execution_id'] != parent['execution_id']
                        or existing['capability_key'] != binding.key
                        or existing['contract_version'] != binding.contract_version
                        or existing['descriptor']['inputs'] != inputs
                        or existing['descriptor']['resource_scope'] != resource_scope(binding.scope(inputs))):
                    raise ValueError('invocation identity input conflict')
                invocation = existing
            else:
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

    def admit_in_uow(self, uow, run, parent, definition, operation_id):
        """Shared bounded admission for the Harness and fixed parent adapters."""
        if run['source_kind'] == 'work':
            from work_domain.progress import ProgressBlocked, blocked_reason
            work = RunLifecycleService.check_work(uow, run)
            reason = blocked_reason(uow, work)
            if reason:
                raise ProgressBlocked(reason)
        # Serialize cross-process admission; no physical ticket survives this transaction.
        uow.execute("SELECT pg_advisory_xact_lock(4830020601)")
        prior = uow.execute(
            "SELECT 1 FROM capability_invocations WHERE invocation_id=%s",
            (operation_id,),
        ).fetchone()
        if not prior:
            counts = uow.execute(
                "SELECT count(*) AS total,count(*) FILTER(WHERE account_id=%s) AS account,"
                "count(*) FILTER(WHERE parent_execution_id=%s) AS parent FROM run_executions "
                "WHERE role='capability' AND status IN ('queued','running','cancelling')",
                (run["account_id"], parent["execution_id"]),
            ).fetchone()
            limits = CapacityConfig.from_environment()
            if (
                counts["parent"]
                or counts["total"] >= limits.capability_global_executions
                or counts["account"] >= limits.capability_account_executions
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
                        from resources.run_budget import RunBudgetExhausted
                        from resources.work_budget import WorkBudgetExhausted
                        error = WorkBudgetExhausted if table == 'work_budgets' else RunBudgetExhausted
                        raise error("aggregate invocation budget exhausted")

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
        current = self.facts.registry.get(invocation['capability_key'], invocation['contract_version'])
        if current != descriptor['definition']:
            from agent_activities.text_result import compatible_text_definition
            from research_domain.capability import compatible_research_definition
            if not (compatible_text_definition(descriptor['definition'], current)
                    or compatible_research_definition(descriptor['definition'], current)):
                raise ResourceDenied('registered capability implementation changed')
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
        response = await asyncio.to_thread(self._observe, request)
        # Observation is committed before raising the control condition. Retrying cannot
        # duplicate the sample or transcript; no further model call is needed to stop.
        reason = await asyncio.to_thread(self.protection, request.account_id, request.run_id)
        if reason:
            raise ApplicationError(reason, type=reason, non_retryable=True)
        return response

    def protection(self, account_id, run_id):
        from work_domain.progress import blocked_reason
        with UnitOfWork(self.store.database_url) as uow:
            run = RunLifecycleService.lock(uow, UUID(account_id), UUID(run_id))
            if run['source_kind'] == 'work':
                work = RunLifecycleService.check_work(uow, run)
                return blocked_reason(uow, work)
        return None

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
                from work_domain.output_evidence import register_text_output

                evidence = register_text_output(uow, self.facts, request.operation_id, result)
                if evidence:
                    event["output_exists_evidence"] = evidence
                    event["message"]["content"] = json.dumps(
                        {**result, "acceptance_evidence": evidence}, ensure_ascii=False,
                    )
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
