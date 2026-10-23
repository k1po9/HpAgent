"""Bounded Work brief from frozen input; never loads a Conversation transcript."""

import asyncio
import json
import re
from uuid import UUID

from application.execution_contracts import ExecutionRequest
from application.prompt_catalog import agent_prompt
from persistence.uow import UnitOfWork
from run_domain.execution import assert_execution_fence, lock_execution
from run_domain.lifecycle import RunLifecycleService


class WorkContextProvider:
    def __init__(self, database):
        self.database = database

    async def load(self, run_id):
        return await asyncio.to_thread(self._load, UUID(run_id))

    def _load(self, run_id):

        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            from run_domain.execution_context import execution_fence
            fence = execution_fence.get()
            execution = lock_execution(uow, fence[0], str(run_id), fence[2]) if fence else None
            if execution and execution["role"] == "subagent":
                brief = execution["context_manifest"]["brief"]
                return ExecutionRequest(
                    str(execution["execution_id"]), str(execution["account_id"]), None, None,
                    brief["objective"], (
                        {"role": "system", "content": agent_prompt("subagent")},
                        {"role": "user", "content": json.dumps(brief, ensure_ascii=False)},
                    ), metadata={"run_id": str(run_id), "surface": "work",
                        "parent_execution_id": str(execution["parent_execution_id"])},
                )
            run = uow.execute(
                "SELECT r.*,e.execution_id FROM runs r JOIN run_executions e "
                "USING(account_id,run_id) WHERE r.run_id=%s AND e.role='root'",
                (run_id,),
            ).fetchone()
            if run is None or run["executor_key"] != "work_agent":
                raise ValueError("Generic Work Run unavailable")
            RunLifecycleService.check_work(uow, run)
            snapshot = run["input_snapshot"]
            from workspace.resources import ResourcePolicy
            # Filter metadata through current grants as well as the frozen candidate set.
            candidates = ResourcePolicy(self.database).candidates_in_uow(uow, run["account_id"], run_id, limit=20)
            budget = uow.execute(
                "SELECT limits,used,reserved FROM run_budgets WHERE run_id=%s", (run_id,)
            ).fetchone()
            from persistence.repositories import FileRepository
            files = FileRepository().list_ready_for_run(uow, run["account_id"], run_id)
            run_files = [{"logical_name": f["logical_name"], "direction": f["direction"],
                          "content_type": f.get("content_type"), "size_bytes": f["size_bytes"]}
                         for f in files[:20]]
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
                "run_files": dto(run_files),
                "run_file_count": len(files),
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
                    "content": agent_prompt("worker"),
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


def _generic_result_object(content):
    """Accept one explicit JSON result; prose alone never supplies a result."""
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except ValueError:
        # Providers can wrap the explicit final object despite the raw-JSON
        # instruction. Exactly one final JSON fence is unambiguous; do not scan
        # arbitrary prose/braces or choose among multiple candidate blocks.
        blocks = list(re.finditer(r"(?m)^```json[ \t]*\r?\n([\s\S]*?)^```[ \t]*$", content))
        if len(blocks) != 1 or content[blocks[0].end():].strip() or content.count("```") != 2:
            return None
        try:
            return json.loads(blocks[0].group(1))
        except ValueError:
            return None


def generic_result(uow, run, work, content, result_ref):
    """An Agent recommends a typed result; persisted facts determine acceptance."""
    from work_domain.models import bounded, continuation

    result = _generic_result_object(content)
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
        if evidence.get("type") not in {"output_exists", "check_passed", "workspace_saved"}:
            raise ValueError("Generic completion needs typed result evidence, not arbitrary successful tools")
        from run_domain.acceptance import validate_typed_evidence
        validate_typed_evidence(uow, run, evidence)
    from run_domain.delegation import required_branch_gaps
    gaps = required_branch_gaps(uow, run["account_id"], run["run_id"])
    if gaps and result["kind"] == "deliverable_ready":
        result["kind"] = "progress_saved"
        result["continuation"] = continuation("blocked", "required_branch_missing:" + ",".join(gaps))
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
    suggest_completion = result["kind"] == "deliverable_ready"
    criteria = run["input_snapshot"].get("requirement", {}).get("acceptance_criteria", [])
    if suggest_completion and any(c["required"] and "user_acceptance" in c["evidence_types"] for c in criteria):
        result["continuation"] = continuation("awaiting_input", "user_acceptance_required")
        suggest_completion = False
    return result, suggest_completion
