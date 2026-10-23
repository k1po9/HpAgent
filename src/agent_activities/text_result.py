"""Publish bounded literal Markdown through the immutable output/result kernel."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, UUID, uuid5

from temporalio import activity

from agent_activities.fencing import fenced_activity
from agent_workflows.capability_contracts import CapabilityExecutionInput
from persistence.uow import UnitOfWork
from resources.run_budget import RunBudgetService
from run_domain.capability_contracts import CapabilityDefinition, CapabilityResult, ResultRef
from workspace.file_scope import RunFileScope


def text_definition():
    return CapabilityDefinition(
        key="text", contract_version=1, implementation_version="text-v1",
        description="发布简短文字/Markdown 持久成果。提交 title 和完整 body（需要时附实际来源链接），返回不可变 ResultRef；不调用模型、不生成 HTML、不要求长期保存。之后用 result__evidence 为匹配的 output_exists 条件登记证据，再返回 deliverable_ready。Publishing alone does not complete Work or claim factual verification.",
        input_schema={"type": "object", "additionalProperties": False,
            "required": ["title", "body"], "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 120, "pattern": r"\S"},
                "body": {"type": "string", "minLength": 1, "maxLength": 16000, "pattern": r"\S"}}},
        output_schema={"type": "object", "additionalProperties": False,
            "properties": {k: {} for k in ("schema_version", "outcome", "summary", "outputs", "checks", "gaps", "error", "provenance")},
            "required": ["schema_version", "outcome", "outputs", "checks"]},
        executor_kind="fixed_workflow", execution_mode="isolated_execution",
        allowed_surfaces=("work",), required_services=("text",), resource_operations=(),
        side_effect_policy="candidate_output",
        limits={"schema_version": 1, "max_duration_seconds": 60, "max_model_calls": 0, "max_tool_calls": 0},
        retry_policy={"schema_version": 1, "max_attempts": 3},
        cancellation_policy="cooperative_bounded", verifier_keys=(),
    )


class TextActivities:
    def __init__(self, capabilities, publisher):
        self.capabilities, self.publisher = capabilities, publisher
        self.database = capabilities.store.database_url

    @activity.defn(name="produce_text_activity")
    @fenced_activity
    async def produce(self, request: CapabilityExecutionInput) -> str:
        return await asyncio.to_thread(self._produce, request)

    def _produce(self, request):
        self.capabilities.store.begin_operation(request.operation_id, request.run_id, "tool")
        with UnitOfWork(self.database) as uow:
            descriptor = self.capabilities.invocation_context(uow, request)
            if uow.execute("SELECT 1 FROM capability_results WHERE invocation_id=%s", (request.invocation_id,)).fetchone():
                return f"capability-result:{request.invocation_id}"
            inputs = descriptor["inputs"]
        document = "# " + inputs["title"].strip() + "\n\n" + inputs["body"].strip() + "\n"
        amounts = {"bytes_written": len(document.encode()), "output_file_bytes": len(document.encode())}
        RunBudgetService(self.database).reserve(UUID(request.run_id), request.operation_id, amounts)
        identity = uuid5(NAMESPACE_URL, request.invocation_id)
        name, operation = f"text-{identity}.md", f"text:{identity}:markdown"
        with TemporaryDirectory(prefix="hpagent-text-") as root:
            base = Path(root)
            for directory in ("inputs", "scratch", "outputs"):
                (base / directory).mkdir()
            scope = RunFileScope(UUID(request.run_id), base / "inputs", base / "scratch", base / "outputs", ())
            output = self.publisher.replay(scope, operation, name)
            if output is None:
                (scope.outputs_root / name).write_text(document, encoding="utf-8")
                output = self.publisher.publish(scope, operation, name, "text/markdown", encoding="utf-8")
        ref = ResultRef(str(uuid5(identity, "ref")), "markdown", str(output.file_id), str(output.file_id),
            request.invocation_id, request.execution_id, request.operation_id, output.sha256)
        return self.capabilities.commit_result(request,
            CapabilityResult("succeeded", "Published immutable Markdown; content quality is not verified.", outputs=(ref,)),
            usage=amounts)
