"""Local HTML generation. Only the parent may save, adopt or complete a goal."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, UUID, uuid5

from temporalio import activity

from agent_activities.fencing import fenced_activity
from agent_workflows.capability_contracts import CapabilityExecutionInput
from persistence.uow import UnitOfWork
from resources.model_budget_context import model_budget_scope
from run_domain.capability_contracts import CapabilityResult, Check, ResultRef
from workspace.file_scope import RunFileScope
from workspace.resources import ResourceDenied

from .generator import ArtifactGenerationError
from .publisher import publish


class HtmlActivities:
    def __init__(self, capabilities, generator, publisher):
        self.capabilities, self.generator, self.publisher = capabilities, generator, publisher
        self.database = capabilities.store.database_url

    def _inputs(self, request):
        with UnitOfWork(self.database) as uow:
            descriptor = self.capabilities.invocation_context(uow, request)
            if uow.execute(
                "SELECT 1 FROM capability_results WHERE invocation_id=%s", (request.invocation_id,)
            ).fetchone():
                return None
            run, _ = self.capabilities.facts._context(uow)
            if (
                run["executor_key"] == "artifact_html"
                and request.invocation_id == f"html:{request.run_id}:invoke:v1"
            ):
                snapshot = run["input_snapshot"]
                target = UUID(
                    snapshot.get("artifact_version_id")
                    or snapshot["requirement"]["spec"]["artifact_version_id"]
                )
                uow.execute(
                    "UPDATE artifact_versions SET status='running',producing_run_id=%s,producing_execution_id=%s,producing_operation_id=%s,started_at=GREATEST(now(),created_at),completed_at=NULL,failure_code=NULL,failure_message=NULL WHERE account_id=%s AND artifact_version_id=%s AND producing_run_id IS NULL",
                    (
                        UUID(request.run_id),
                        UUID(request.execution_id),
                        request.operation_id,
                        run["account_id"],
                        target,
                    ),
                )
            inputs = descriptor["inputs"]
            source = inputs.get("text", "")
            if "source_ref" in inputs:
                source = self._read(uow, UUID(inputs["source_ref"]))
            previous, artifact_id, parent_id = None, None, None
            if "previous_ref" in inputs:
                ref = self.capabilities.facts.read_ref(uow, UUID(inputs["previous_ref"]))
                if ref.type != "artifact_version":
                    raise ResourceDenied("previous_ref must identify an exact HTML version")
                previous = self._read(uow, UUID(inputs["previous_ref"]))
                artifact_id, parent_id = UUID(ref.object_id), UUID(ref.version)
            return source, inputs.get("instruction"), previous, artifact_id, parent_id

    def _read(self, uow, ref_id):
        ref = self.capabilities.facts.read_ref(uow, ref_id)
        if ref.type == "artifact_version":
            row = uow.execute(
                "SELECT html FROM artifact_versions WHERE account_id=%s AND artifact_version_id=%s AND status='completed'",
                (UUID(self._account()), UUID(ref.version)),
            ).fetchone()
            if not row:
                raise ResourceDenied("HTML version unavailable")
            return row["html"]
        if ref.type not in {"markdown", "file"}:
            raise ResourceDenied("HTML source must be text or an artifact")
        row = uow.execute(
            "SELECT storage_key,size_bytes FROM stored_files WHERE account_id=%s AND file_id=%s AND status='ready'",
            (UUID(self._account()), UUID(ref.object_id)),
        ).fetchone()
        if not row or row["size_bytes"] > 1024 * 1024:
            raise ResourceDenied("HTML source unavailable or too large")
        with self.publisher.store.open(row["storage_key"]) as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or (
            ref.sha256 and hashlib.sha256(data).hexdigest() != ref.sha256
        ):
            raise ResourceDenied("HTML source digest/size conflict")
        return data.decode("utf-8")

    @staticmethod
    def _account():
        from run_domain.execution_context import execution_fence

        return execution_fence.get()[0]

    def _output(self, request, kind, content):
        identity = uuid5(NAMESPACE_URL, request.invocation_id)
        name = f"html-{identity}.{kind}"
        operation = f"html:{identity}:{kind}"
        with TemporaryDirectory(prefix="hpagent-html-") as root:
            base = Path(root)
            for name_part in ("inputs", "scratch", "outputs"):
                (base / name_part).mkdir()
            scope = RunFileScope(
                UUID(request.run_id), base / "inputs", base / "scratch", base / "outputs", ()
            )
            replay = self.publisher.replay(scope, operation, name)
            if replay:
                return replay
            (scope.outputs_root / name).write_text(content, encoding="utf-8")
            return self.publisher.publish(
                scope,
                operation,
                name,
                "text/html" if kind == "html" else "application/json",
                encoding="utf-8",
            )

    @activity.defn(name="produce_html_activity")
    @fenced_activity
    async def produce(self, request: CapabilityExecutionInput) -> str:
        await asyncio.to_thread(
            self.capabilities.store.begin_operation, request.operation_id, request.run_id, "tool"
        )
        values = await asyncio.to_thread(self._inputs, request)
        if values is None:
            return f"capability-result:{request.invocation_id}"
        source, instruction, previous, artifact, parent = values
        # Recover immutable output before spending again after a lost Activity response.
        identity = uuid5(NAMESPACE_URL, request.invocation_id)
        with UnitOfWork(self.database) as uow:
            self.capabilities.invocation_context(uow, request)
            saved = uow.execute(
                "SELECT f.storage_key FROM output_publish_operations o JOIN stored_files f USING(account_id,file_id) WHERE o.run_id=%s AND o.operation_id=%s AND o.status='completed'",
                (UUID(request.run_id), f"html:{identity}:html"),
            ).fetchone()
        from web_artifacts.generator import WebArtifactGenerator

        validator = WebArtifactGenerator(
            None, max_bytes=getattr(self.generator, "max_bytes", 1024 * 1024)
        )
        try:
            if saved:
                with self.publisher.store.open(saved["storage_key"]) as stream:
                    document = stream.read().decode("utf-8")
            else:
                with model_budget_scope(
                    request.account_id,
                    request.run_id,
                    request.operation_id,
                    execution_attempt=request.execution_attempt,
                    phase="artifact_generation",
                    read_timeout_seconds=getattr(self.generator, "read_timeout_seconds", 90.0),
                ):
                    document = await self.generator.generate(
                        source_markdown=source, instruction=instruction, previous_html=previous
                    )
            document = validator.harden(document)
        except ArtifactGenerationError as exc:
            return await asyncio.to_thread(
                self.capabilities.commit_result,
                request,
                CapabilityResult(
                    "insufficient",
                    exc.safe_message,
                    error={
                        "code": exc.code,
                        "safe_message": exc.safe_message,
                        "retry_class": "transient"
                        if exc.retryable or exc.code in {"artifact_invalid_html", "artifact_html_too_large"}
                        else "requires_input",
                    },
                ),
            )
        from resources.run_budget import RunBudgetService

        budget = RunBudgetService(self.database)
        amounts = {
            "bytes_written": len(document.encode()) + 1024,
            "output_file_bytes": len(document.encode()) + 1024,
        }
        await asyncio.to_thread(budget.reserve, UUID(request.run_id), request.operation_id, amounts)
        html_file = await asyncio.to_thread(self._output, request, "html", document)
        checks = {
            "schema_version": 1,
            "verifier": "html.basic",
            "version": "1",
            "passed": True,
            "scope": ["complete_html", "size_limit", "preview_csp"],
            "browser_interactions": "not_checked",
        }
        check_file = await asyncio.to_thread(
            self._output, request, "json", json.dumps(checks, sort_keys=True)
        )
        return await asyncio.to_thread(
            self._commit, request, document, source, artifact, parent, html_file, check_file
        )

    def _commit(self, request, document, source, artifact, parent, html_file, check_file):
        version = uuid5(NAMESPACE_URL, request.invocation_id + ":html-version")
        artifact = artifact or uuid5(NAMESPACE_URL, request.invocation_id + ":artifact")
        subject = ResultRef(
            str(uuid5(version, "ref")),
            "artifact_version",
            str(artifact),
            str(version),
            request.invocation_id,
            request.execution_id,
            request.operation_id,
            html_file.sha256,
        )
        evidence = ResultRef(
            str(uuid5(version, "checks")),
            "check_evidence",
            str(check_file.file_id),
            str(check_file.file_id),
            request.invocation_id,
            request.execution_id,
            request.operation_id,
            check_file.sha256,
        )
        with UnitOfWork(self.database) as uow:
            self.capabilities.invocation_context(uow, request)
            run, _ = self.capabilities.facts._context(uow)
            if (
                run["executor_key"] == "artifact_html"
                and request.invocation_id == f"html:{request.run_id}:invoke:v1"
            ):
                snapshot = run["input_snapshot"]
                target = UUID(
                    snapshot.get("artifact_version_id")
                    or snapshot["requirement"]["spec"]["artifact_version_id"]
                )
                pending = uow.execute(
                    "SELECT * FROM artifact_versions WHERE account_id=%s AND artifact_version_id=%s FOR UPDATE",
                    (run["account_id"], target),
                ).fetchone()
                artifact = pending["artifact_id"]
                if pending["producing_run_id"] in (None, UUID(request.run_id)):
                    version = target
                subject = ResultRef(
                    str(uuid5(version, "ref")),
                    "artifact_version",
                    str(artifact),
                    str(version),
                    request.invocation_id,
                    request.execution_id,
                    request.operation_id,
                    html_file.sha256,
                )
                if pending["producing_run_id"] is None:
                    uow.execute(
                        "UPDATE artifact_versions SET status='running',producing_run_id=%s,producing_execution_id=%s,producing_operation_id=%s,started_at=GREATEST(now(),created_at),completed_at=NULL,failure_code=NULL,failure_message=NULL WHERE account_id=%s AND artifact_version_id=%s",
                        (
                            UUID(request.run_id),
                            UUID(request.execution_id),
                            request.operation_id,
                            run["account_id"],
                            version,
                        ),
                    )
            publish(
                uow,
                UUID(request.run_id),
                artifact,
                version,
                document,
                operation_id=request.operation_id,
                markdown=source,
                parent_version_id=parent,
                file_id=html_file.file_id,
            )
            self.capabilities.facts.publish_result(
                uow,
                invocation_id=request.invocation_id,
                producing_operation_id=request.operation_id,
                result=CapabilityResult(
                    "succeeded",
                    "HTML version ready; browser interactions not checked.",
                    outputs=(subject, evidence),
                    checks=(Check("html.basic", "1", subject.ref_id, "passed", evidence.ref_id),),
                ),
            )
            from resources.run_budget import RunBudgetService

            RunBudgetService(self.database).settle_in_uow(
                uow,
                UUID(request.run_id),
                request.operation_id,
                {
                    "bytes_written": html_file.size_bytes + check_file.size_bytes,
                    "output_file_bytes": html_file.size_bytes + check_file.size_bytes,
                },
                "measured",
            )
        return f"capability-result:{request.invocation_id}"
