"""Bounded Work brief from frozen input; never loads a Conversation transcript."""

import asyncio
import json
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
