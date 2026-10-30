"""Publish bounded Markdown or DOCX through the immutable output/result kernel."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, UUID, uuid5

from temporalio import activity

from agent_activities.fencing import fenced_activity
from agent_workflows.capability_contracts import CapabilityExecutionInput
from persistence.uow import UnitOfWork
from resources.run_budget import RunBudgetService
from run_domain.capability_contracts import CapabilityDefinition, CapabilityResult, Check, ResultRef
from workspace.file_scope import RunFileScope


def admit_text(uow, run, inputs):
    targets = (run.get('input_snapshot') or {}).get('requirement', {}).get('deliverables')
    if targets and inputs.get('format', 'markdown') not in {t['format'] for t in targets['items']}:
        raise ValueError('output format does not match Requirement deliverables')


def compatible_text_definition(frozen, current):
    """Only the reviewed, additive text/v1 upgrade can resume old descriptors."""
    if frozen.get('key') != 'text' or frozen.get('contract_version') != 1:
        return False
    old, new = deepcopy(frozen), deepcopy(current)
    old.pop('description', None)
    new.pop('description', None)
    if old == new:
        return True
    # Old Markdown invocations cannot acquire a new format input or verifier.
    if 'format' in old['input_schema']['properties'] or old['verifier_keys']:
        return False
    new['input_schema']['properties'].pop('format', None)
    new['verifier_keys'] = []
    return old == new


def text_definition():
    return CapabilityDefinition(
        key="text", contract_version=1, implementation_version="text-v1",
        description="发布可下载文字成果，format=markdown（默认）或 docx。提交 title 和完整 body，按 Requirement 的交付格式选择；发布 Markdown 已产生 .md 文件，不需再包装成 Word。返回准确 ResultRef 和可信格式检查。父侧可自动登记明确目标的证据，其余用 result__evidence。发布不证明内容质量、保存或 Work 完成。",
        input_schema={"type": "object", "additionalProperties": False,
            "required": ["title", "body"], "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 120, "pattern": r"\S"},
                "format": {"type": "string", "enum": ["markdown", "docx"], "default": "markdown"},
                "body": {"type": "string", "minLength": 1, "maxLength": 16000, "pattern": r"\S"}}},
        output_schema={"type": "object", "additionalProperties": False,
            "properties": {k: {} for k in ("schema_version", "outcome", "summary", "outputs", "checks", "gaps", "error", "provenance")},
            "required": ["schema_version", "outcome", "outputs", "checks"]},
        executor_kind="fixed_workflow", execution_mode="isolated_execution",
        allowed_surfaces=("work",), required_services=("text",), resource_operations=(),
        side_effect_policy="candidate_output",
        limits={"schema_version": 1, "max_duration_seconds": 60, "max_model_calls": 0, "max_tool_calls": 0},
        retry_policy={"schema_version": 1, "max_attempts": 3},
        cancellation_policy="cooperative_bounded", verifier_keys=('file.format.markdown', 'file.format.docx'),
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
            required = uow.execute('SELECT input_snapshot FROM runs WHERE run_id=%s',
                                   (UUID(request.run_id),)).fetchone()['input_snapshot']
        document = "# " + inputs["title"].strip() + "\n\n" + inputs["body"].strip() + "\n"
        format = inputs.get('format', 'markdown')
        check_required = bool(required.get('requirement', {}).get('deliverables')) or format == 'docx'
        identity = uuid5(NAMESPACE_URL, request.invocation_id)
        name = f"text-{identity}.{'md' if format == 'markdown' else 'docx'}"
        operation = f"text:{identity}:{format}"
        content_type = ('text/markdown' if format == 'markdown' else
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        with TemporaryDirectory(prefix="hpagent-text-") as root:
            base = Path(root)
            for directory in ("inputs", "scratch", "outputs"):
                (base / directory).mkdir()
            scope = RunFileScope(UUID(request.run_id), base / "inputs", base / "scratch", base / "outputs", ())
            output = self.publisher.replay(scope, operation, name)
            if output is None:
                if format == 'markdown':
                    (scope.outputs_root / name).write_text(document, encoding="utf-8")
                else:
                    from file_adapters import DocxWriter
                    DocxWriter().create(scope.outputs_root, name, [
                        {'kind': 'heading', 'level': 1, 'text': inputs['title']},
                        *({'kind': 'paragraph', 'text': line} for line in inputs['body'].splitlines() if line.strip()),
                    ])
                size = (scope.outputs_root / name).stat().st_size
            else:
                size = output.size_bytes
            # Include the bounded machine-readable verification receipt in output budgets.
            amounts = {'bytes_written': size + (2048 if check_required else 0),
                       'output_file_bytes': size + (2048 if check_required else 0)}
            RunBudgetService(self.database).reserve(UUID(request.run_id), request.operation_id, amounts)
            if output is None:
                output = self.publisher.publish(scope, operation, name, content_type,
                    encoding='utf-8' if format == 'markdown' else 'binary')
            ref = ResultRef(str(uuid5(identity, 'ref')), 'markdown' if format == 'markdown' else 'file',
                str(output.file_id), str(output.file_id), request.invocation_id, request.execution_id,
                request.operation_id, output.sha256)
            if not check_required:
                return self.capabilities.commit_result(request,
                    CapabilityResult('succeeded', 'Published immutable Markdown; content quality is not verified.',
                        outputs=(ref,)), usage=amounts)
            with UnitOfWork(self.database) as uow:
                self.capabilities.invocation_context(uow, request)
                stored = uow.execute('SELECT storage_key FROM stored_files WHERE account_id=%s AND file_id=%s',
                                     (UUID(request.account_id), output.file_id)).fetchone()
            with self.publisher.store.open(stored['storage_key']) as stream:
                data = stream.read(1024 * 1024 + 1)
            from file_runtime.format_check import verify_format
            passed = len(data) <= 1024 * 1024 and verify_format(data, format, content_type, name, output.sha256)
            check_name = f'format-{identity}.json'
            (scope.outputs_root / check_name).write_text(json.dumps({
                'schema_version': 1, 'subject_ref': ref.ref_id, 'sha256': output.sha256,
                'format': format, 'verifier_version': '1', 'passed': passed,
            }), encoding='utf-8')
            check_output = self.publisher.publish(scope, f'text:{identity}:format', check_name, 'application/json', encoding='utf-8')
            check_ref = ResultRef(str(uuid5(identity, 'format')), 'check_evidence',
                str(check_output.file_id), str(check_output.file_id), request.invocation_id,
                request.execution_id, request.operation_id, check_output.sha256)
            check = Check(f'file.format.{format}', '1', ref.ref_id, 'passed' if passed else 'failed', check_ref.ref_id)
            actual = size + check_output.size_bytes
            return self.capabilities.commit_result(request,
                CapabilityResult('succeeded' if passed else 'insufficient',
                    'Published immutable output with byte-verified format; content quality is not verified.',
                    outputs=(ref, check_ref), checks=(check,)),
                usage={'bytes_written': actual, 'output_file_bytes': actual})
