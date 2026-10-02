"""Trusted fork/result boundary. Children cannot acquire coordinator permissions."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AgentRunInput,
    RunContext,
    RunSource,
    ToolExecutionResult,
)
from agent_workflows.delegation_contracts import (
    DELEGATE_TOOL,
    MAX_BRANCHES,
    NON_RETRYABLE_BRANCH_ERRORS,
    BranchFinishInput,
    DelegationFinishInput,
    DelegationInput,
    DelegationPrepared,
)
from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.lifecycle import RunLifecycleService
from workspace.resources import ResourcePolicy

from .fencing import execution_fence, fenced_activity
from .store import AgentDataStore


class DelegationDenied(ValueError):
    pass


def validate_briefs(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {"branches"}:
        raise DelegationDenied("delegation requires structured branches")
    briefs = arguments["branches"]
    if not isinstance(briefs, list) or not 1 <= len(briefs) <= MAX_BRANCHES:
        raise DelegationDenied("delegation allows one to three branches")
    if len(json.dumps(briefs, ensure_ascii=False).encode()) > 12288:
        raise DelegationDenied("delegation briefs exceed size limit")
    keys = set()
    fields = {
        "branch_key",
        "objective",
        "constraints",
        "output_contract",
        "node_ids",
        "file_ids",
        "tool_names",
        "required",
    }
    for brief in briefs:
        if not isinstance(brief, dict) or set(brief) != fields:
            raise DelegationDenied("invalid branch brief")
        key = brief["branch_key"]
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", key)
            or key in keys
        ):
            raise DelegationDenied("invalid or duplicate branch key")
        keys.add(key)
        for name, maximum in (("objective", 2000), ("output_contract", 1000)):
            if not isinstance(brief[name], str) or not 1 <= len(brief[name]) <= maximum:
                raise DelegationDenied("missing bounded branch objective/output contract")
        if not isinstance(brief["required"], bool):
            raise DelegationDenied("branch required must be boolean")
        for name, maximum in (
            ("constraints", 10),
            ("node_ids", 40),
            ("file_ids", 40),
            ("tool_names", 20),
        ):
            values = brief[name]
            if (
                not isinstance(values, list)
                or len(values) > maximum
                or any(not isinstance(v, str) or not 1 <= len(v) <= 500 for v in values)
                or len(set(values)) != len(values)
            ):
                raise DelegationDenied("invalid branch scope/constraints")
        for value in brief["node_ids"] + brief["file_ids"]:
            if str(UUID(value)) != value:
                raise DelegationDenied("resource IDs must be canonical UUIDs")
        if DELEGATE_TOOL in brief["tool_names"]:
            raise DelegationDenied("recursive delegation is forbidden")
    return briefs


def current_execution(uow):
    fence = execution_fence.get()
    if fence is None:
        return None
    return AgentDataStore._execution(uow, fence[0], fence[1], fence[2])


def child_scope(uow):
    execution = current_execution(uow)
    return execution["resource_scope"] if execution and execution["role"] == "subagent" else None


def require_root_write(uow):
    if child_scope(uow) is not None:
        raise DelegationDenied("Subagent cannot change long-term resources or permissions")


def branch_projection(uow, account_id, run_id):
    """Usage is a projection of original reservations, never another charge."""
    from work_domain.persistence import dto

    rows = uow.execute(
        "SELECT execution_id,parent_execution_id,branch_key,branch_attempt,status,result_ref,error_code,"
        "context_manifest->'brief'->'required' AS required FROM run_executions "
        "WHERE account_id=%s AND run_id=%s AND role='subagent' ORDER BY branch_key,branch_attempt",
        (account_id, run_id),
    ).fetchall()
    for row in rows:
        usage = uow.execute(
            "SELECT dimension,sum(CASE WHEN state='settled' THEN actual_amount ELSE 0 END) AS used,"
            "sum(CASE WHEN state='reserved' THEN reserved_amount ELSE 0 END) AS reserved "
            "FROM run_usage_ledger WHERE run_id=%s AND execution_id=%s GROUP BY dimension",
            (run_id, row["execution_id"]),
        ).fetchall()
        row["usage"] = {
            v["dimension"]: {"used": int(v["used"]), "reserved": int(v["reserved"])} for v in usage
        }
    return dto(rows)


def required_branch_gaps(uow, account_id, run_id):
    return [
        row["branch_key"]
        for row in uow.execute(
            "SELECT b->>'branch_key' AS branch_key FROM execution_delegations d, jsonb_array_elements(d.briefs) b "
            "WHERE d.account_id=%s AND d.run_id=%s AND (b->>'required')::boolean AND NOT EXISTS("
            "SELECT 1 FROM run_executions e WHERE e.parent_execution_id=d.execution_id "
            "AND e.branch_key=b->>'branch_key' AND e.status='succeeded')",
            (account_id, run_id),
        ).fetchall()
    ]


class DelegationActivities:
    def __init__(self, store, run_budget):
        self.store = store
        self.database = store.database_url
        self.run_budget = run_budget

    @activity.defn(name="prepare_delegation_activity")
    @fenced_activity
    async def prepare(self, request: DelegationInput) -> DelegationPrepared:
        if request.tool.schema_version != AGENT_SCHEMA_VERSION or request.branch_attempt not in {
            1,
            2,
        }:
            raise ApplicationError("invalid delegation contract", non_retryable=True)
        prior = await asyncio.to_thread(
            self.store.begin_tool_operation, request.operation_id, request.run_id
        )
        if prior.status == "completed":
            return DelegationPrepared((), ToolExecutionResult(**prior.result_payload))
        arguments = await asyncio.to_thread(
            self.store.tool_call_arguments,
            request.tool.tool_call.arguments_ref,
            request.tool.tool_call.tool_call_id,
        )
        try:
            briefs = validate_briefs(arguments)
            children = await asyncio.to_thread(self._prepare, request, briefs)
            return DelegationPrepared(tuple(children))
        except (ValueError, PermissionError) as exc:
            # Rejection is a tool observation; the root can save progress without another model call.
            return DelegationPrepared((), error=str(exc)[:500])

    @retryable_transaction
    def _prepare(self, request, briefs):
        with UnitOfWork(self.database) as uow:
            AgentDataStore._assert_fence(uow)
            run = AgentDataStore._active_run(uow, request.account_id, request.run_id)
            parent = current_execution(uow)
            if parent["role"] != "root" or run["executor_key"] != "work_agent":
                raise DelegationDenied("only Generic Work root may delegate")
            existing = uow.execute(
                "SELECT * FROM execution_delegations WHERE execution_id=%s",
                (parent["execution_id"],),
            ).fetchone()
            if existing and (
                existing["operation_id"] != request.operation_id or existing["briefs"] != briefs
            ):
                raise DelegationDenied("one immutable delegation per root Run")
            decision_ref = request.tool.tool_call.arguments_ref.rpartition("#")[0]
            decision = uow.execute(
                "SELECT result_payload FROM execution_operations WHERE result_ref=%s "
                "AND execution_id=%s AND status='completed'",
                (decision_ref, parent["execution_id"]),
            ).fetchone()
            manifest = decision["result_payload"].get("delegatable_tools", []) if decision else []
            authority = ResourcePolicy(self.database)
            snapshot = uow.execute(
                "SELECT * FROM run_resource_snapshots WHERE run_id=%s AND status='ready'",
                (run["run_id"],),
            ).fetchone()
            for brief in briefs:
                if not set(brief["tool_names"]) <= set(manifest):
                    raise DelegationDenied("branch tools exceed the parent's read-only manifest")
                for node in brief["node_ids"]:
                    candidate = uow.execute(
                        "SELECT 1 FROM run_resource_candidates WHERE account_id=%s "
                        "AND run_id=%s AND node_id=%s",
                        (run["account_id"], run["run_id"], UUID(node)),
                    ).fetchone()
                    if (
                        not candidate
                        or not snapshot
                        or not authority._grants(
                            uow,
                            run["account_id"],
                            snapshot["subject_kind"],
                            snapshot["subject_id"],
                            UUID(node),
                            "read_content",
                        )
                    ):
                        raise DelegationDenied(
                            "branch node is outside current parent resource scope"
                        )
                for file in brief["file_ids"]:
                    if not authority._file_authorized_in_uow(
                        uow, run["account_id"], run["run_id"], UUID(file)
                    ):
                        raise DelegationDenied(
                            "branch file is outside current parent resource scope"
                        )
            pending = []
            for brief in briefs:
                prior = uow.execute(
                    "SELECT * FROM run_executions WHERE parent_execution_id=%s "
                    "AND branch_key=%s ORDER BY branch_attempt DESC LIMIT 1",
                    (parent["execution_id"], brief["branch_key"]),
                ).fetchone()
                if (
                    request.branch_attempt == 2
                    and prior
                    and prior["error_code"] in NON_RETRYABLE_BRANCH_ERRORS
                ):
                    continue
                if request.branch_attempt == 2 and (
                    not prior or prior["branch_attempt"] == 1 and prior["status"] != "failed"
                ):
                    continue
                if prior and prior["branch_attempt"] == request.branch_attempt:
                    pending.append((brief, prior))
                else:
                    pending.append((brief, None))
            new_count = sum(row is None for _, row in pending)
            if new_count:
                uow.execute("SELECT pg_advisory_xact_lock(4830020501)")
                counts = uow.execute(
                    "SELECT count(*) AS total,count(*) FILTER(WHERE account_id=%s) AS account "
                    "FROM run_executions WHERE role='subagent' AND status IN ('queued','running','cancelling')",
                    (run["account_id"],),
                ).fetchone()
                if counts["total"] + new_count > int(os.getenv("WORK_GLOBAL_SUBAGENTS", "48")) or (
                    counts["account"] + new_count > int(os.getenv("WORK_ACCOUNT_SUBAGENTS", "6"))
                ):
                    raise DelegationDenied("branch admission capacity exhausted")
                for table, key, value in (
                    ("run_budgets", "run_id", run["run_id"]),
                    ("work_budgets", "work_id", run["work_id"]),
                ):
                    budget = uow.execute(
                        f"SELECT limits,used,reserved FROM {table} WHERE {key}=%s", (value,)
                    ).fetchone()
                    if budget and any(
                        budget["limits"].get(d, 0)
                        - budget["used"].get(d, 0)
                        - budget["reserved"].get(d, 0)
                        <= 0
                        for d in ("model_input_tokens", "model_output_tokens", "model_total_tokens")
                    ):
                        raise DelegationDenied(
                            "remaining aggregate token budget cannot admit branches"
                        )
                    if (
                        not budget
                        or budget["limits"].get("model_calls", 0)
                        - budget["used"].get("model_calls", 0)
                        - budget["reserved"].get("model_calls", 0)
                        < new_count
                    ):
                        raise DelegationDenied("remaining aggregate budget cannot admit branches")
            if not existing:
                uow.execute(
                    "INSERT INTO execution_delegations(account_id,run_id,execution_id,operation_id,briefs) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        run["account_id"],
                        run["run_id"],
                        parent["execution_id"],
                        request.operation_id,
                        Jsonb(briefs),
                    ),
                )
            children = []
            for brief, prior in pending:
                identity = uuid5(
                    NAMESPACE_URL,
                    f"hpagent:branch:{parent['execution_id']}:{brief['branch_key']}:{request.branch_attempt}",
                )
                if not prior:
                    context = {
                        "schema_version": 1,
                        "brief": brief,
                        "work_id": str(run["work_id"]),
                        "requirement_revision": run["requirement_revision"],
                        "work_control_epoch": run["work_control_epoch"],
                    }
                    scope = {
                        "schema_version": 1,
                        **{k: brief[k] for k in ("node_ids", "file_ids", "tool_names")},
                    }
                    uow.execute(
                        "INSERT INTO run_executions(execution_id,account_id,run_id,role,parent_execution_id,"
                        "branch_key,branch_attempt,context_manifest,resource_scope) VALUES (%s,%s,%s,'subagent',%s,%s,%s,%s,%s)",
                        (
                            identity,
                            run["account_id"],
                            run["run_id"],
                            parent["execution_id"],
                            brief["branch_key"],
                            request.branch_attempt,
                            Jsonb(context),
                            Jsonb(scope),
                        ),
                    )
                children.append(
                    AgentRunInput(
                        schema_version=AGENT_SCHEMA_VERSION,
                        execution_id=str(identity),
                        run_id=request.run_id,
                        account_id=request.account_id,
                        source=RunSource("work", str(run["work_id"])),
                        context=RunContext(context_ref=f"execution:{identity}", surface="work"),
                        strategy="react",
                        max_turns=6,
                    )
                )
            return children

    @activity.defn(name="finish_branch_activity")
    async def finish_branch(self, request: BranchFinishInput) -> None:
        if request.schema_version != AGENT_SCHEMA_VERSION or request.status not in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            raise ApplicationError("invalid branch result", non_retryable=True)
        await asyncio.to_thread(self._finish_branch, request)

    @retryable_transaction
    def _finish_branch(self, request):
        with UnitOfWork(self.database) as uow:
            run = RunLifecycleService.lock(uow, UUID(request.account_id), UUID(request.run_id))
            row = AgentDataStore._execution(
                uow, request.account_id, request.run_id, request.execution_id
            )
            if row["role"] != "subagent":
                raise ValueError("branch completion cannot finalize root")
            if row["status"] in {"succeeded", "failed", "cancelled"}:
                return
            status = request.status if run["status"] in {"queued", "running"} else "cancelled"
            reference = None
            if status == "succeeded":
                operation = uow.execute(
                    "SELECT o.result_ref FROM execution_operations o "
                    "JOIN execution_result_receipts r USING(operation_id) WHERE o.account_id=%s AND o.run_id=%s "
                    "AND o.execution_id=%s AND o.result_ref=%s AND o.status='completed' AND r.disposition='current'",
                    (
                        UUID(request.account_id),
                        UUID(request.run_id),
                        row["execution_id"],
                        request.result_ref,
                    ),
                ).fetchone()
                if not operation:
                    raise ValueError("branch result needs its own committed receipt")
                reference = request.result_ref
            uow.execute(
                "UPDATE run_executions SET status=%s,result_ref=%s,error_code=%s,updated_at=now() "
                "WHERE execution_id=%s",
                (status, reference, request.error_code, row["execution_id"]),
            )
            uow.execute(
                "UPDATE execution_attempt_leases SET lease_expires_at=NULL,owner_segment_id=NULL,"
                "fencing_token=fencing_token+1 WHERE execution_id=%s",
                (row["execution_id"],),
            )

    @activity.defn(name="finish_delegation_activity")
    @fenced_activity
    async def finish(self, request: DelegationFinishInput) -> ToolExecutionResult:
        previous = await asyncio.to_thread(
            self.store.begin_tool_operation, request.operation_id, request.run_id
        )
        if previous.status == "completed":
            return ToolExecutionResult(**previous.result_payload)
        result = await asyncio.to_thread(self._aggregate, request)
        reference = f"agent-tool-result:{request.operation_id}"
        payload = asdict(
            ToolExecutionResult(
                AGENT_SCHEMA_VERSION,
                request.operation_id,
                reference,
                request.tool.transcript_version,
                "Delegation results saved",
                tool_success=result["success"],
            )
        )
        version = await asyncio.to_thread(
            self.store.complete_operation_with_event,
            transcript_id=request.tool.transcript_id,
            expected_version=request.tool.transcript_version,
            event_type="tool_result",
            operation_id=request.operation_id,
            event_payload={
                "message": {
                    "role": "tool",
                    "tool_call_id": request.tool.tool_call.tool_call_id,
                    "name": DELEGATE_TOOL,
                    "content": json.dumps(result, ensure_ascii=False),
                },
                "raw_result": result,
            },
            result_ref=reference,
            result_payload=payload,
        )
        payload["transcript_version"] = version
        return ToolExecutionResult(**payload)

    def _aggregate(self, request):
        with UnitOfWork(self.database) as uow:
            AgentDataStore._assert_fence(uow)
            delegation = uow.execute(
                "SELECT briefs FROM execution_delegations WHERE execution_id=%s AND operation_id=%s",
                (UUID(request.execution_id), request.operation_id),
            ).fetchone()
            if not delegation:
                return {
                    "success": False,
                    "gaps": ["delegation rejected by scope, budget or capacity policy"],
                    "branches": [],
                }
            rows = branch_projection(uow, UUID(request.account_id), UUID(request.run_id))
            latest = {row["branch_key"]: row for row in rows}
            gaps = []
            for brief in delegation["briefs"]:
                row = latest[brief["branch_key"]]
                if row["status"] not in {"succeeded", "failed", "cancelled"}:
                    raise ValueError("delegation cannot aggregate active branches")
                if row["result_ref"]:
                    content = uow.execute(
                        "SELECT result_payload->>'content' AS content FROM execution_operations "
                        "WHERE execution_id=%s AND result_ref=%s AND status='completed'",
                        (UUID(row["execution_id"]), row["result_ref"]),
                    ).fetchone()
                    row["summary"] = (content["content"] or "")[:2000]
                if row["status"] != "succeeded":
                    gaps.append(
                        {
                            "branch_key": brief["branch_key"],
                            "required": brief["required"],
                            "error": row["error_code"],
                        }
                    )
            return {
                "success": not any(g["required"] for g in gaps),
                "gaps": gaps,
                "branches": rows,
                "result_policy": "Root must verify references; required gaps forbid completion. No additional charge for aggregation.",
            }
