"""Top-level Research adapter: save, optional display and parent acceptance policy."""

from __future__ import annotations

import asyncio
import html
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from temporalio import activity

from orchestration.research_workflow import ResearchStageRef, ResearchWorkflowInput
from persistence.uow import UnitOfWork, retryable_transaction
from research_domain.context import (
    invocation_id,
    research_activity,
    research_identity,
    research_key,
)
from run_domain.execution import assert_execution_fence
from run_domain.lifecycle import RunLifecycleService
from run_domain.results import complete_operation
from workspace.catalog import WorkspaceCatalog


class ResearchParentActivities:
    def __init__(self, research, capabilities):
        self.research, self.capabilities = research, capabilities
        self.database, self.trace = research.database, research.trace
        self._ref, self._root_event_id = research._ref, research._root_event_id

    def _load_report(self, uow, run_id):
        self.capabilities.facts.read_result(uow, invocation_id())
        return uow.execute(
            "SELECT * FROM research_reports WHERE run_id=%s AND invocation_id=%s",
            (run_id, invocation_id()),
        ).fetchone()

    def _publish_view(self, uow, run_id, document):
        report = self._load_report(uow, run_id)
        artifact_id = uuid5(NAMESPACE_URL, f"{invocation_id()}:view")
        version_id = uuid5(NAMESPACE_URL, f"{invocation_id()}:view:v1")
        from web_artifacts.publisher import publish

        publish(
            uow,
            run_id,
            artifact_id,
            version_id,
            document,
            operation_id=f"research:{research_identity(run_id)}:PublishArtifact:v1",
            title="Research Report",
            markdown=report["report_markdown"],
        )
        uow.execute(
            "UPDATE research_reports SET artifact_id=%s,artifact_version_id=%s WHERE invocation_id=%s",
            (artifact_id, version_id, invocation_id()),
        )

    @activity.defn
    async def prepare_research_parent_activity(self, request: ResearchWorkflowInput):
        from web_domain.lifecycle import WebRunLifecycleService

        try:
            info = activity.info()
        except RuntimeError:
            info = None
        authority = await asyncio.to_thread(
            WebRunLifecycleService(self.database).prepare,
            UUID(request.run_id),
            info.workflow_id if info else None,
            info.workflow_run_id if info else None,
        )
        if authority.status in {"cancelling", "cancelled"}:
            raise asyncio.CancelledError
        await asyncio.to_thread(
            self.trace.create_trace_run, UUID(request.run_id), {"run_kind": "research"}
        )
        await asyncio.to_thread(
            self.trace.start_event,
            UUID(request.run_id),
            self._root_event_id(UUID(request.run_id)),
            None,
            "ResearchParent",
            "research_run",
            {},
        )
        # The caller tool Operation + immutable invocation descriptor are the admission facts.
        # No Run-keyed Research instance/stage is created for this parent-only boundary.
        return await self.research._run_stage(
            UUID(request.run_id), "AdmitResearch", lambda: self._admit(request)
        )

    def _child(self, invocation):
        return self.capabilities.child_input(invocation)

    def _admit(self, request):
        operation_id = f"research:{request.run_id}:invoke:v1"
        self.research.store.begin_operation(operation_id, request.run_id, "tool")
        with UnitOfWork(self.database) as uow:
            run, caller = self.capabilities.facts._context(uow)
            task = uow.execute(
                "SELECT objective,spec FROM work_requirements WHERE account_id=%s "
                "AND work_id=%s AND revision=%s",
                (run["account_id"], run["work_id"], run["requirement_revision"]),
            ).fetchone()
            # History candidates are the only child file scope; permissions are rechecked on use.
            nodes = uow.execute(
                "SELECT node_id FROM run_resource_candidates WHERE account_id=%s AND run_id=%s",
                (run["account_id"], run["run_id"]),
            ).fetchall()
            from workspace.resources import ResourcePolicy

            policy = ResourcePolicy(uow)
            nodes = [
                node
                for node in nodes
                if policy._grants(
                    uow, run["account_id"], "work", run["work_id"], node["node_id"], "read_content"
                )
            ]
            definition = self.capabilities.facts.registry.get("research", 1)
            self.capabilities.admit_in_uow(uow, run, caller, definition, operation_id)
            row = self.capabilities.facts.create(
                uow,
                operation_id=operation_id,
                capability_key="research",
                contract_version=1,
                inputs={
                    "objective": task["objective"],
                    "source_strategy": task["spec"]["source_strategy"],
                },
                scope={
                    "schema_version": 1,
                    "node_ids": [str(n["node_id"]) for n in nodes],
                    "file_ids": [],
                    "tool_names": [],
                    "result_ids": [],
                },
                invocation_limits=definition["limits"],
            )
            return self._child(row)

    @activity.defn
    @research_activity
    async def publish_research_artifact_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)

        return await self.research._run_stage(
            run_id, "PublishArtifact", lambda: self._publish(run_id)
        )

    @retryable_transaction
    def _publish(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            report = self._load_report(uow, run_id)
            if report is None:
                raise LookupError(f"research report not found: {run_id}")
            if report["artifact_version_id"] is not None:
                return 1
            structured = report["report_structured_json"]
            if isinstance(structured, str):
                structured = json.loads(structured)
            title = html.escape(str(structured.get("title") or "Research Report"))
            markdown = html.escape(str(report["report_markdown"]))
            run = uow.execute(
                "SELECT r.run_id,r.created_at,q.objective FROM runs r JOIN work_requirements q "
                "ON q.account_id=r.account_id AND q.work_id=r.work_id AND q.revision=r.requirement_revision WHERE r.run_id=%s",
                (run_id,),
            ).fetchone()
            claims = uow.execute(
                "SELECT cl.statement,cl.importance,cl.evidence_status,COALESCE(jsonb_agg(DISTINCT jsonb_build_object('title',s.title,'uri',s.canonical_uri)) FILTER (WHERE s.source_id IS NOT NULL),'[]'::jsonb) AS sources FROM research_claims cl LEFT JOIN research_citations c ON c.claim_id=cl.claim_id LEFT JOIN evidence_items e ON e.evidence_id=c.evidence_id LEFT JOIN source_records s ON s.source_id=e.source_id WHERE cl.research_id=%s GROUP BY cl.claim_id ORDER BY cl.ordinal",
                (research_key(run_id),),
            ).fetchall()
            findings = "".join(
                f"<li><strong>{html.escape(str(row['importance']))}</strong> "
                f"{html.escape(str(row['statement']))} "
                f"<em>({html.escape(str(row['evidence_status']))})</em></li>"
                for row in claims
            )
            source_map: dict[str, str] = {}
            for row in claims:
                for source in row["sources"]:
                    source_map[str(source["uri"])] = str(source["title"] or source["uri"])
            sources_html = "".join(
                f"<li><a href='{html.escape(uri, quote=True)}'>{html.escape(label)}</a></li>"
                for uri, label in sorted(source_map.items())
            )
            document = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{title}</title></head><body><article><h1>{title}</h1>"
                f"<p><time>{html.escape(run['created_at'].isoformat())}</time> · Run "
                f"<code>{run_id}</code></p><h2>Summary</h2>"
                f"<p>{html.escape(str(run['objective']))}</p><h2>Key Findings</h2>"
                f"<ul>{findings}</ul><h2>Sources / Citations</h2><ol>{sources_html}</ol>"
                f"<h2>Research Report (Markdown truth source)</h2>"
                f"<pre style='white-space:pre-wrap'>{markdown}</pre>"
                f"<h2>Research metadata</h2><dl><dt>Citation status</dt>"
                f"<dd>{html.escape(str(report['citation_status']))}</dd></dl>"
                "</article></body></html>"
            )
            self._publish_view(uow, run_id, document)
            return 1

    @activity.defn
    @research_activity
    async def save_research_workspace_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self.research._run_stage(
            run_id, "WorkspaceSave", lambda: self._save_workspace(run_id)
        )

    def _save_workspace(self, run_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            intent = uow.execute(
                "SELECT * FROM research_run_save_intents WHERE run_id=%s", (run_id,)
            ).fetchone()
            if intent is None or intent["state"] == "succeeded":
                return
            report = self._load_report(uow, run_id)
            output = (
                {"file_id": report["markdown_file_id"]}
                if report and report["markdown_file_id"]
                else None
            )
            run = uow.execute(
                "SELECT r.created_at,q.timing->>'timezone' AS schedule_timezone FROM runs r JOIN work_requirements q "
                "ON q.account_id=r.account_id AND q.work_id=r.work_id AND q.revision=r.requirement_revision WHERE r.run_id=%s",
                (run_id,),
            ).fetchone()
        if output is None:
            with UnitOfWork(self.database) as uow:
                assert_execution_fence(uow)
                uow.execute(
                    "UPDATE research_run_save_intents SET failure_code=%s WHERE run_id=%s "
                    "AND state='pending'",
                    ("research_markdown_output_missing", run_id),
                )
            raise RuntimeError("research_markdown_output_missing")
        from zoneinfo import ZoneInfo

        local_day = run["created_at"].astimezone(ZoneInfo(run["schedule_timezone"])).date()
        name = f"research-{local_day.isoformat()}-{run_id.hex}.md"
        try:
            catalog = WorkspaceCatalog(self.database)
            if intent["operation"] == "create_child":
                entry_id = catalog.save_file(
                    intent["account_id"],
                    intent["target_directory_id"],
                    output["file_id"],
                    name,
                    intent["operation_id"],
                    agent_run_id=run_id,
                )
            else:
                result = catalog.update_file(
                    intent["account_id"],
                    intent["target_entry_id"],
                    run_id,
                    output["file_id"],
                    intent["expected_revision"],
                    intent["expected_sha256"],
                    intent["operation_id"],
                    work_auto=True,
                )
                entry_id = UUID(result["node_id"])
        except Exception as exc:
            with UnitOfWork(self.database) as uow:
                assert_execution_fence(uow)
                uow.execute(
                    "UPDATE research_run_save_intents SET failure_code=%s WHERE run_id=%s "
                    "AND state='pending'",
                    (type(exc).__name__, run_id),
                )
            raise
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            uow.execute(
                "UPDATE research_run_save_intents SET state='succeeded',entry_id=%s,"
                "saved_at=now(),failure_code=NULL WHERE run_id=%s AND state='pending'",
                (entry_id, run_id),
            )

    @activity.defn
    @research_activity
    async def complete_research_activity(self, request: ResearchWorkflowInput) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        with UnitOfWork(self.database) as uow:
            status = uow.execute(
                "SELECT r.status FROM runs r JOIN capability_invocations i USING(account_id,run_id) WHERE r.run_id=%s AND i.invocation_id=%s",
                (run_id, invocation_id()),
            ).fetchone()
        if status is None:
            raise ValueError("Research parent invocation does not belong to Run")
        if status["status"] != "succeeded":
            await self.research._run_stage(run_id, "Complete", lambda: self._complete(run_id))
        return self._ref(run_id, "Complete", {"item_count": 1})

    @retryable_transaction
    def _complete(self, run_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            run = uow.execute(
                "SELECT account_id,conversation_id,status FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
            required = uow.execute(
                "SELECT i.state,i.operation,i.entry_id,"
                "CASE WHEN i.operation='create_child' THEN EXISTS ("
                "SELECT 1 FROM workspace_save_operations o WHERE o.account_id=i.account_id "
                "AND o.operation_id=i.operation_id AND o.node_id=i.entry_id "
                "AND o.source_kind='work_auto' AND o.source_run_id=i.run_id) "
                "ELSE EXISTS (SELECT 1 FROM workspace_version_operations o "
                "WHERE o.account_id=i.account_id AND o.operation_id=i.operation_id "
                "AND o.node_id=i.entry_id AND o.source_kind='work_auto' "
                "AND o.source_run_id=i.run_id) END AS committed "
                "FROM research_run_save_intents i WHERE i.run_id=%s AND i.required",
                (run_id,),
            ).fetchone()
            if required is not None and (
                required["state"] != "succeeded" or not required["committed"]
            ):
                raise RuntimeError("required_workspace_save_incomplete")
        if run is None:
            raise LookupError(f"research Run not found: {run_id}")
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            owner = uow.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            report = self._load_report(uow, run_id)
            requirement = uow.execute(
                "SELECT acceptance_criteria FROM work_requirements WHERE account_id=%s "
                "AND work_id=%s AND revision=%s",
                (owner["account_id"], owner["work_id"], owner["requirement_revision"]),
            ).fetchone()
        if report is None or report["markdown_file_id"] is None:
            raise RuntimeError("research_report_not_published")
        self.research.store.begin_operation(invocation_id(), str(run_id), "tool")
        with UnitOfWork(self.database) as uow:
            result = self.capabilities.facts.read_result(uow, invocation_id())
            if result["outcome"] != "succeeded":
                raise RuntimeError("research_insufficient")
            complete_operation(
                uow,
                invocation_id(),
                f"research-parent-result:{uuid5(NAMESPACE_URL, invocation_id())}",
                result,
            )
        evidence = [
            {
                "criterion_id": c["id"],
                "type": "research_report",
                "ref": str(report["markdown_file_id"]),
            }
            for c in requirement["acceptance_criteria"]
            if "research_report" in c["evidence_types"]
        ]
        # Keep the existing Artifact-only user acceptance command usable when a view exists.
        # Markdown remains the required Research fact; a missing view never invalidates it.
        if report["artifact_version_id"]:
            evidence.extend(
                {**item, "ref": str(report["artifact_version_id"])} for item in list(evidence)
            )
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            owner = RunLifecycleService.lock(uow, run["account_id"], run_id)
            requires_acceptance = any(
                c["required"] and "user_acceptance" in c["evidence_types"]
                for c in requirement["acceptance_criteria"]
            )
            result = {"schema_version": 1, "kind": "deliverable_ready", "evidence": evidence}
            if requires_acceptance:
                result["continuation"] = {
                    "schema_version": 1,
                    "kind": "awaiting_input",
                    "reason": "user_acceptance_required",
                    "receipt_ref": str(report["artifact_version_id"] or report["markdown_file_id"]),
                }
            RunLifecycleService(self.database).finish_in_uow(
                uow, owner, "succeeded", result=result, accept_result=not requires_acceptance
            )
            uow.execute(
                "UPDATE workflow_executions SET status='completed',closed_at=COALESCE(closed_at,now()),updated_at=now(),version=version+1 WHERE run_id=%s AND is_current AND status IN ('scheduled','running')",
                (run_id,),
            )
        self.trace.finish_event(
            run_id,
            uuid5(NAMESPACE_URL, f"hpagent:research:{run_id}:root"),
            "completed",
            {"outcome": "report_ready"},
        )

    @activity.defn
    @research_activity
    async def fail_research_activity(self, request: ResearchWorkflowInput) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        cancelled = await self.research._run_stage(run_id, "Failed", lambda: self._fail(run_id))
        result = self._ref(run_id, "Failed", {"item_count": 0})
        if cancelled:
            result["cancelled"] = True
        return result

    @retryable_transaction
    def _fail(self, run_id: UUID) -> bool:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            run = uow.execute(
                "SELECT account_id,conversation_id,status FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
            if run is not None and run["status"] in {"cancelling", "cancelled"}:
                return True
            intent = uow.execute(
                "SELECT failure_code FROM research_run_save_intents WHERE run_id=%s "
                "AND state='pending'",
                (run_id,),
            ).fetchone()
            if intent is not None and intent["failure_code"]:
                uow.execute(
                    "UPDATE research_run_save_intents SET state='failed' WHERE run_id=%s",
                    (run_id,),
                )
        if run is None:
            raise LookupError(f"research Run not found: {run_id}")
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            owner = RunLifecycleService.lock(uow, run["account_id"], run_id)
            RunLifecycleService(self.database).finish_in_uow(
                uow,
                owner,
                "failed",
                failure_code="workspace_save_failed"
                if intent is not None and intent["failure_code"]
                else "research_stage_failed",
                failure_message="Research parent delivery failed.",
            )
            uow.execute(
                "UPDATE workflow_executions SET status='failed',closed_at=COALESCE(closed_at,now()),updated_at=now(),version=version+1 WHERE run_id=%s AND is_current AND status IN ('scheduled','running')",
                (run_id,),
            )
        return False
