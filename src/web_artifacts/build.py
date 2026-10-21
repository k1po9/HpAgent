"""Fixed Artifact command adapter over the same local HTML capability as the Harness."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

from agent_activities.store import AgentDataStore
from agent_workflows.capability_contracts import CapabilityClose, CapabilityExecutionInput
from agent_workflows.lifecycle_contracts import SegmentInput
from persistence.uow import UnitOfWork
from run_domain.execution_context import fence_scope
from run_domain.lifecycle import RunLifecycleService
from run_domain.results import complete_operation
from web_artifacts.capability import html_scope


class ArtifactBuildService:
    def __init__(self, database, generator, capabilities=None, publisher=None):
        self.database, self.generator = database, generator
        self.store = AgentDataStore(database)
        if capabilities is None:
            from agent_activities.capabilities import CapabilityActivities, CapabilityBinding
            from run_domain.capabilities import CapabilityFacts
            from run_domain.capability_contracts import CapabilityRegistry
            from web_artifacts.capability import html_definition

            capabilities = CapabilityActivities(
                self.store,
                CapabilityFacts(
                    CapabilityRegistry((html_definition(),)), services=frozenset({"html"})
                ),
                (CapabilityBinding("html", 1, "HtmlCapabilityWorkflow", html_scope),),
            )
        if publisher is None:
            import os

            from file_runtime.output import OutputPublisher
            from storage.tenant_file_store import TenantFileStore

            if os.getenv("FILE_STORE_ROOT"):
                publisher = OutputPublisher(
                    database,
                    TenantFileStore(os.environ["FILE_STORE_ROOT"], max_bytes=2 * 1024 * 1024),
                )
        self.capabilities = capabilities
        self.publisher = publisher

    def _root(self, run_id, name, callback):
        identity = self.store.run_identity(str(run_id))
        segment = SegmentInput(
            2,
            str(run_id),
            identity["account_id"],
            f"{name}:{uuid4()}",
            execution_id=identity["execution_id"],
        )
        token = self.store.acquire_segment(segment)
        try:
            with fence_scope(identity["account_id"], str(run_id), token, identity["execution_id"]):
                return callback(identity)
        finally:
            self.store.release_segment(segment)

    def prepare(self, run_id):
        def admit(identity):
            operation = f"html:{run_id}:invoke:v1"
            self.store.begin_operation(operation, str(run_id), "tool")
            with UnitOfWork(self.database) as uow:
                run, caller = self.capabilities.facts._context(uow)
                snapshot = run["input_snapshot"]
                version_id = UUID(
                    snapshot.get("artifact_version_id")
                    or snapshot["requirement"]["spec"]["artifact_version_id"]
                )
                version = uow.execute(
                    "SELECT v.*,a.conversation_id FROM artifact_versions v JOIN artifacts a USING(account_id,artifact_id) WHERE v.account_id=%s AND v.artifact_version_id=%s",
                    (run["account_id"], version_id),
                ).fetchone()
                if not version or (
                    run["source_kind"] == "chat"
                    and version["conversation_id"] not in (None, run["conversation_id"])
                ):
                    raise ValueError("Artifact command input unavailable")
                inputs = {
                    "text": version["source_markdown"],
                    "instruction": version["instruction"] or "",
                }
                if version["parent_version_id"]:
                    prior = uow.execute(
                        "SELECT ref_id FROM capability_result_refs WHERE account_id=%s AND artifact_version_id=%s",
                        (run["account_id"], version["parent_version_id"]),
                    ).fetchone()
                    if not prior:
                        raise ValueError("Previous version has no immutable capability reference")
                    self.capabilities.facts.adopt(uow, prior["ref_id"])
                    inputs["previous_ref"] = str(prior["ref_id"])
                definition = self.capabilities.facts.registry.get("html", 1)
                self.capabilities.admit_in_uow(uow, run, caller, definition, operation)
                invocation = self.capabilities.facts.create(
                    uow,
                    operation_id=operation,
                    capability_key="html",
                    contract_version=1,
                    inputs=inputs,
                    scope=html_scope(inputs),
                    invocation_limits=definition["limits"],
                )
                return CapabilityExecutionInput(
                    identity["account_id"],
                    str(run_id),
                    str(invocation["child_execution_id"]),
                    operation,
                    "HtmlCapabilityWorkflow",
                    600,
                    3,
                    operation + ":produce",
                )

        return self._root(run_id, f"html:{run_id}:admit", admit)

    def terminal(self, run_id):
        with UnitOfWork(self.database) as uow:
            row = uow.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            if row and row["status"] in {"succeeded", "failed", "cancelled"}:
                version = uow.execute(
                    "SELECT artifact_version_id FROM artifact_versions WHERE producing_run_id=%s ORDER BY version DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
                return {
                    "status": "completed" if row["status"] == "succeeded" else row["status"],
                    "artifact_version_id": str(version["artifact_version_id"]) if version else None,
                }
        return None

    def complete(self, child):
        terminal = self.terminal(UUID(child.run_id))
        if terminal:
            return terminal

        def finish(identity):
            self.store.begin_operation(child.invocation_id, child.run_id, "tool")
            with UnitOfWork(self.database) as uow:
                run, _ = self.capabilities.facts._context(uow)
                result = self.capabilities.facts.read_result(uow, child.invocation_id)
                if result["outcome"] != "succeeded":
                    RunLifecycleService(self.database).finish_in_uow(
                        uow,
                        run,
                        "failed",
                        failure_code=(result["error"] or {}).get("code", "html_insufficient"),
                        failure_message=result["summary"],
                    )
                    return {"status": "failed"}
                output = next(r for r in result["outputs"] if r["type"] == "artifact_version")
                if run["work_id"]:
                    from uuid6 import uuid7

                    uow.execute(
                        "INSERT INTO work_artifacts(reference_id,account_id,work_id,artifact_version_id,source_requirement_revision,role) VALUES (%s,%s,%s,%s,%s,'deliverable') ON CONFLICT DO NOTHING",
                        (
                            uuid7(),
                            run["account_id"],
                            run["work_id"],
                            UUID(output["version"]),
                            run["requirement_revision"],
                        ),
                    )
                complete_operation(
                    uow, child.invocation_id, f"html-parent:{child.invocation_id}", result
                )
                requirement = run["input_snapshot"].get("requirement", {})
                evidence = [
                    {"criterion_id": c["id"], "type": "artifact_version", "ref": output["version"]}
                    for c in requirement.get("acceptance_criteria", [])
                    if "artifact_version" in c["evidence_types"]
                ]
                result = {"schema_version": 1, "kind": "deliverable_ready", "evidence": evidence}
                needs_acceptance = any(
                    c["required"] and "user_acceptance" in c["evidence_types"]
                    for c in requirement.get("acceptance_criteria", [])
                )
                if needs_acceptance:
                    from work_domain.models import continuation

                    result["continuation"] = continuation(
                        "awaiting_input", "user_acceptance_required", receipt_ref=output["version"]
                    )
                RunLifecycleService(self.database).finish_in_uow(
                    uow,
                    run,
                    "succeeded",
                    content="HTML 成果已生成。",
                    result=result,
                    accept_result=bool(run["work_id"]) and not needs_acceptance,
                )
                return {"artifact_version_id": output["version"], "status": "completed"}

        return self._root(UUID(child.run_id), f"html:{child.run_id}:complete", finish)

    async def execute(self, version_id, *, run_id, workflow_id=None, attempt=1):
        """Direct test/fake adapter; production Workflow releases parent before child execution."""
        from web_artifacts.local import HtmlActivities

        terminal = await asyncio.to_thread(self.terminal, run_id)
        if terminal:
            return terminal
        child = await asyncio.to_thread(self.prepare, run_id)
        segment = SegmentInput(
            2,
            child.run_id,
            child.account_id,
            f"html:{child.invocation_id}:{attempt}",
            execution_id=child.execution_id,
        )
        token = await asyncio.to_thread(self.store.acquire_segment, segment)
        try:
            await HtmlActivities(self.capabilities, self.generator, self.publisher).produce(
                replace(child, lease_token=token, execution_attempt=attempt)
            )
        finally:
            await asyncio.to_thread(self.store.release_segment, segment)
        await self.capabilities.close(CapabilityClose(child, "succeeded"))
        return await asyncio.to_thread(self.complete, child)
