"""Bounded Work brief from frozen input; never loads a Conversation transcript."""

import asyncio
import json
from uuid import UUID

from application.execution_contracts import ExecutionRequest
from persistence.uow import UnitOfWork
from run_domain.lifecycle import RunLifecycleService


class WorkContextProvider:
    def __init__(self, database):
        self.database = database

    async def load(self, run_id):
        return await asyncio.to_thread(self._load, UUID(run_id))

    def _load(self, run_id):
        from agent_activities.store import AgentDataStore

        with UnitOfWork(self.database) as uow:
            AgentDataStore._assert_fence(uow)
            run = uow.execute(
                "SELECT r.*,e.execution_id FROM runs r JOIN run_executions e "
                "USING(account_id,run_id) WHERE r.run_id=%s",
                (run_id,),
            ).fetchone()
            if run is None or run["executor_key"] != "work_agent":
                raise ValueError("Generic Work Run unavailable")
            RunLifecycleService.check_work(uow, run)
            snapshot = run["input_snapshot"]
            candidates = uow.execute(
                "SELECT node_id,logical_name,fixed_revision FROM run_resource_candidates "
                "WHERE account_id=%s AND run_id=%s ORDER BY node_id LIMIT 40",
                (run["account_id"], run_id),
            ).fetchall()
            budget = uow.execute(
                "SELECT limits FROM run_budgets WHERE run_id=%s", (run_id,)
            ).fetchone()
            # Candidate metadata is advisory; selecting/using bytes rechecks current grants.
            from work_domain.persistence import dto

            brief = {
                "schema_version": 1,
                "work_id": str(run["work_id"]),
                "requirement_revision": run["requirement_revision"],
                "requirement": snapshot["requirement"],
                "checkpoint": snapshot["checkpoint"],
                "trigger": snapshot["wakeup_id"],
                "resources": dto(candidates),
                "artifact_refs": snapshot.get("artifact_refs", []),
                "budget": dto(budget),
                "input_ref": f"run:{run_id}",
            }
        text = json.dumps(brief, ensure_ascii=False)
        return ExecutionRequest(
            str(run["execution_id"]),
            str(run["account_id"]),
            None,
            None,
            snapshot["requirement"]["objective"],
            (
                {
                    "role": "system",
                    "content": "Execute this fixed Work brief within authorized resources. "
                    "Save progress or state missing input. A narrative answer cannot complete the mandate. "
                    "Do not create or manage other Work. No implicit channel or resource permission is granted. "
                    "Finish with a JSON result: schema_version=1; kind=deliverable_ready/progress_saved/waiting_input/waiting_due; "
                    "evidence=[{criterion_id,type:operation_receipt,ref:exact_tool_result_ref}]; "
                    "continuation={schema_version:1,kind:ready/at_time/awaiting_input/blocked,reason,due_at?}. "
                    "Only use verified tool receipts for required criteria. Missing input or evidence saves progress; "
                    "request completion only when all acceptance criteria are met. Do not claim user acceptance.",
                },
                {"role": "user", "content": text},
            ),
            metadata={"run_id": str(run_id), "surface": "work"},
        )


class RunRequestLoader:
    def __init__(self, database, chat_loader):
        self.database = database
        self.chat_loader = chat_loader
        self.work = WorkContextProvider(database)

    async def load(self, run_id):
        def source():
            with UnitOfWork(self.database) as uow:
                row = uow.execute(
                    "SELECT source_kind FROM runs WHERE run_id=%s", (UUID(run_id),)
                ).fetchone()
                if row is None:
                    raise ValueError("Run unavailable")
                return row["source_kind"]

        return await (
            self.work.load(run_id)
            if await asyncio.to_thread(source) == "work"
            else self.chat_loader.load(run_id)
        )


class RunExecutionBindings:
    def resource_key(self, request):
        return f"{request.execution_id}:{request.lease_token}"

    def transcript_context(self, request):
        chat = request.context.chat
        return {
            "conversation_id": chat.conversation_id if chat else None,
            "session_id": None,
            "execution_id": request.execution_id,
        }

    def validate_loaded(self, request, loaded):
        chat = request.context.chat
        if (
            loaded.account_id != request.account_id
            or loaded.execution_id != request.execution_id
            or (loaded.conversation_id != (chat.conversation_id if chat else None))
        ):
            raise ValueError("context identity mismatch")


def generic_result(uow, run, work, content, result_ref):
    """An Agent recommends a typed result; persisted facts determine acceptance."""
    from work_domain.models import bounded, continuation

    try:
        result = json.loads(content)
    except (ValueError, TypeError):
        result = None
    if not isinstance(result, dict) or result.get("schema_version") != 1 or "kind" not in result:
        # Narrative is retained as a candidate, never interpreted as completion evidence.
        result = {
            "schema_version": 1,
            "kind": "progress_saved",
            "evidence": [],
            "continuation": continuation("awaiting_input", "result_requires_acceptance"),
        }
    bounded(result, {"schema_version", "kind", "evidence", "continuation", "checkpoint"})
    evidence_items = result.get("evidence", [])
    if not isinstance(evidence_items, list) or len(evidence_items) > 40:
        raise ValueError("invalid Generic evidence list")
    for evidence in evidence_items:
        if not isinstance(evidence, dict):
            raise ValueError("invalid Generic evidence")
        if evidence.get("type") != "operation_receipt":
            raise ValueError("Generic executor cannot manufacture acceptance evidence")
        reference = evidence.get("ref")
        receipt = uow.execute(
            "SELECT 1 FROM execution_operations o JOIN execution_result_receipts r USING(operation_id) "
            "WHERE o.account_id=%s AND o.run_id=%s AND o.result_ref=%s "
            "AND o.status='completed' AND r.disposition='current' AND o.operation_type='tool' "
            "AND o.result_payload->>'tool_success'='true'",
            (run["account_id"], run["run_id"], reference),
        ).fetchone()
        if not receipt:
            raise ValueError("Generic acceptance needs a verified tool receipt from this Run")
    if "checkpoint" not in result:
        result["checkpoint"] = {
            "schema_version": 1,
            "checkpoint_version": work["checkpoint"]["checkpoint_version"] + 1,
            "requirement_revision": run["requirement_revision"],
            "source_run_id": str(run["run_id"]),
            "facts": [],
            "decisions": [],
            "unresolved": [],
            "next_steps": [],
            "evidence_refs": [result_ref],
        }
    return result, result["kind"] == "deliverable_ready"
