"""A finite artifact build belongs to its own Work Run and root execution."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid5

from agent_activities.fencing import fence_scope
from agent_activities.store import AgentDataStore
from agent_workflows.lifecycle_contracts import SegmentInput
from persistence.uow import UnitOfWork
from resources.model_budget_context import model_budget_scope
from run_domain.lifecycle import RunLifecycleService
from web_artifacts.publisher import publish
from work_domain.models import continuation

from .generator import ArtifactGenerationError, WebArtifactGenerator


class ArtifactBuildService:
    def __init__(self, database: object, generator: WebArtifactGenerator):
        self.database, self.generator = database, generator
        self.store = AgentDataStore(database)

    async def execute(self, version_id: UUID, *, run_id: UUID, workflow_id=None, attempt=1):
        identity = await asyncio.to_thread(self.store.run_identity, str(run_id))
        if identity["status"] == "succeeded":
            return {"artifact_version_id": str(version_id), "status": "completed"}
        segment = SegmentInput(
            2,
            str(run_id),
            identity["account_id"],
            f"artifact:{run_id}:{attempt}",
            execution_id=identity["execution_id"],
        )
        token = await asyncio.to_thread(self.store.acquire_segment, segment)
        operation = f"artifact:{run_id}:generate:v1"
        try:
            with fence_scope(identity["account_id"], str(run_id), token, identity["execution_id"]):
                await asyncio.to_thread(
                    self.store.begin_operation, operation, str(run_id), "artifact_build"
                )
                inputs = await asyncio.to_thread(self._prepare, version_id, run_id, operation)
                version_id = inputs["artifact_version_id"]
                with model_budget_scope(
                    identity["account_id"],
                    str(run_id),
                    operation,
                    phase="artifact_generation",
                    execution_attempt=attempt,
                    final_response=True,
                    artifact_id=str(inputs["artifact_id"]),
                    artifact_version_id=str(version_id),
                    workflow_id=workflow_id,
                    read_timeout_seconds=getattr(self.generator, "read_timeout_seconds", 90.0),
                ):
                    html = await self.generator.generate(
                        source_markdown=inputs["source_markdown"],
                        instruction=inputs["instruction"],
                        previous_html=inputs["previous_html"],
                    )
                await asyncio.to_thread(self._complete, version_id, run_id, operation, html)
                return {"artifact_version_id": str(version_id), "status": "completed"}
        except ArtifactGenerationError as exc:
            await asyncio.to_thread(self._fail, version_id, run_id, exc.code, exc.safe_message)
            raise
        finally:
            await asyncio.shield(asyncio.to_thread(self.store.release_segment, segment))

    def _prepare(self, version_id, run_id, operation):
        with UnitOfWork(self.database) as uow:
            run = RunLifecycleService.lock(
                uow, UUID(self.store.run_identity(str(run_id))["account_id"]), run_id
            )
            AgentDataStore._assert_fence(uow)
            RunLifecycleService.check_work(uow, run)
            if run["input_snapshot"]["requirement"]["spec"]["artifact_version_id"] != str(
                version_id
            ):
                raise ValueError("artifact version is outside fixed build input")
            row = uow.execute(
                "SELECT v.*,p.html AS previous_html FROM artifact_versions v LEFT JOIN artifact_versions p "
                "ON p.account_id=v.account_id AND p.artifact_version_id=v.parent_version_id "
                "WHERE v.account_id=%s AND v.artifact_version_id=%s FOR UPDATE OF v",
                (run["account_id"], version_id),
            ).fetchone()
            if not row:
                raise ValueError("artifact producing execution conflict")
            if row["producing_run_id"] not in (None, run_id):
                # A new Run produces a new version; prior execution provenance stays immutable.
                uow.execute(
                    "SELECT artifact_id FROM artifacts WHERE account_id=%s AND artifact_id=%s FOR UPDATE",
                    (run["account_id"], row["artifact_id"]),
                )
                output_id = uuid5(run_id, "artifact-output-v1")
                number = uow.execute(
                    "SELECT max(version)+1 AS n FROM artifact_versions WHERE account_id=%s AND artifact_id=%s",
                    (run["account_id"], row["artifact_id"]),
                ).fetchone()["n"]
                uow.execute(
                    "INSERT INTO artifact_versions(artifact_version_id,account_id,artifact_id,version,parent_version_id,instruction,source_markdown) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(artifact_version_id) DO NOTHING",
                    (
                        output_id,
                        run["account_id"],
                        row["artifact_id"],
                        number,
                        row["parent_version_id"],
                        row["instruction"],
                        row["source_markdown"],
                    ),
                )
                row = uow.execute(
                    "SELECT v.*,p.html AS previous_html FROM artifact_versions v LEFT JOIN artifact_versions p "
                    "ON p.account_id=v.account_id AND p.artifact_version_id=v.parent_version_id "
                    "WHERE v.account_id=%s AND v.artifact_version_id=%s FOR UPDATE OF v",
                    (run["account_id"], output_id),
                ).fetchone()
                version_id = output_id
            execution = uow.execute(
                "SELECT execution_id FROM run_executions WHERE run_id=%s AND role='root'", (run_id,)
            ).fetchone()
            if row["status"] != "completed":
                uow.execute(
                    "UPDATE artifact_versions SET status='running',producing_run_id=%s,producing_execution_id=%s,producing_operation_id=%s,"
                    "started_at=COALESCE(started_at,now()),completed_at=NULL,html=NULL,failure_code=NULL,failure_message=NULL,updated_at=now() WHERE artifact_version_id=%s",
                    (run_id, execution["execution_id"], operation, version_id),
                )
            return dict(row)

    def _complete(self, version_id, run_id, operation, html):
        with UnitOfWork(self.database) as uow:
            identity = uow.execute(
                "SELECT account_id FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
            run = RunLifecycleService.lock(uow, identity["account_id"], run_id)
            AgentDataStore._assert_fence(uow)
            row = uow.execute(
                "SELECT artifact_id FROM artifact_versions WHERE account_id=%s AND artifact_version_id=%s",
                (run["account_id"], version_id),
            ).fetchone()
            publish(uow, run_id, row["artifact_id"], version_id, html, operation_id=operation)
            AgentDataStore._complete_operation(
                uow,
                operation,
                f"artifact-version:{version_id}",
                {"artifact_version_id": str(version_id), "side_effect_class": "idempotent_write"},
            )
            requirement = run["input_snapshot"]["requirement"]
            evidence = [
                {"criterion_id": c["id"], "type": "artifact_version", "ref": str(version_id)}
                for c in requirement["acceptance_criteria"]
                if "artifact_version" in c["evidence_types"]
            ]
            user_confirmation = any(
                c["required"] and "user_acceptance" in c["evidence_types"]
                for c in requirement["acceptance_criteria"]
            )
            result = {"schema_version": 1, "kind": "deliverable_ready", "evidence": evidence}
            if user_confirmation:
                result["continuation"] = continuation(
                    "awaiting_input", "user_acceptance_required", receipt_ref=str(version_id)
                )
            RunLifecycleService(self.database).finish_in_uow(
                uow, run, "succeeded", result=result, accept_result=not user_confirmation
            )

    def _fail(self, version_id, run_id, code, message):
        with UnitOfWork(self.database) as uow:
            identity = uow.execute(
                "SELECT account_id FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
            run = RunLifecycleService.lock(uow, identity["account_id"], run_id)
            if run["status"] != "running":
                return
            AgentDataStore._assert_fence(uow)
            uow.execute(
                "UPDATE artifact_versions SET status='failed',html=NULL,failure_code=%s,failure_message=%s,completed_at=now(),updated_at=now() WHERE artifact_version_id=%s AND producing_run_id=%s AND status='running'",
                (code, message[:1000], version_id, run_id),
            )
