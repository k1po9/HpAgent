from __future__ import annotations

import pytest

from actions.contracts import ActionRequest, ActionResult
from actions.runtime import ActionRuntime


def test_action_result_preserves_explicit_failure_semantics() -> None:
    request = ActionRequest("call-1", "read_file", {})
    result = ActionResult.from_runtime_result(
        request,
        {"success": False, "output": None, "error": "mock tool failed"},
    )

    assert result.success is False
    assert result.failed is True
    assert result.error == "mock tool failed"
    assert result.display_result is None


def test_action_result_legacy_error_still_counts_as_failure() -> None:
    result = ActionResult(ActionRequest("call-1", "legacy", {}), error="failed")
    assert result.success is None
    assert result.failed is True


@pytest.mark.asyncio
async def test_action_runtime_exception_returns_uniform_failure(caplog) -> None:
    class Sandbox:
        async def execute(self, tool_name, arguments):
            raise RuntimeError("adapter exploded")

    class Sandboxes:
        def get_sandbox_for_execution(self, session_id):
            return Sandbox()

    result = await ActionRuntime(sandbox_manager=Sandboxes())._execute(
        tool_name="broken", arguments={}, resource_key="session", execution_id="run"
    )

    assert result == {
        "success": False,
        "output": None,
        "error": "adapter exploded",
        "metadata": {},
    }
    record = next(r for r in caplog.records if getattr(r, 'event', '') == 'tool_runtime_failed')
    assert record.execution_id == "run" and record.resource_key == "session"
    assert record.error_type == "RuntimeError" and record.traceback
    assert "adapter exploded" not in record.getMessage()


@pytest.mark.asyncio
async def test_action_runtime_rejects_empty_execution_identity() -> None:
    runtime = ActionRuntime()
    with pytest.raises(ValueError, match="resource_key and execution_id are required"):
        await runtime.select_tools(
            user_content="hello", resource_key="session", execution_id=""
        )


async def test_selection_is_fresh_for_context_hints_and_permissions():
    from types import SimpleNamespace

    from sandbox.tools.routing.models import ToolSelectionResult

    calls = []

    class Sandbox:
        async def select_tools(self, query, limit):
            calls.append(query)
            return ToolSelectionResult(({'name': str(len(calls))},), {'queries': [query]})

    runtime = ActionRuntime(sandbox_manager=SimpleNamespace(get_sandbox_for_execution=lambda _: Sandbox()))
    results = [await runtime.select_tools(user_content='same', group_context_text=context,
                                         resource_key='r', execution_id='e')
               for context in ('first', 'second', 'second')]
    assert [r.tool_schemas[0]['name'] for r in results] == ['1', '2', '3']
    assert calls == ['same\n--- 群聊 ---\nfirst', 'same\n--- 群聊 ---\nsecond', 'same\n--- 群聊 ---\nsecond']


async def test_missing_sandbox_is_failed_selection_not_empty_tools():
    result = await ActionRuntime().select_tools(resource_key='r', execution_id='e')
    assert result.status == 'failed' and not result.tool_schemas
    assert result.error.code == 'tool_retrieval_sandbox_unavailable'


def test_result_json_matches_persisted_json_and_limits_structure():
    import json

    from actions.contracts import iter_result_json

    value = {'中文': ['"\\\n\t', None, True, 1.5], 2: '😀'}
    assert ''.join(iter_result_json(value)) == json.dumps(value)
    cyclic = []
    cyclic.append(cyclic)
    for invalid in (cyclic, object(), [None] * 100001):
        with pytest.raises((ValueError, TypeError)):
            ''.join(iter_result_json(invalid))
    nested = None
    for _ in range(66):
        nested = [nested]
    with pytest.raises(ValueError, match='depth'):
        ''.join(iter_result_json(nested))


@pytest.mark.parametrize('extra', [0, 1])
def test_inline_limit_counts_complete_json_representation(extra):
    import json

    raw = {'success': True, 'output': '', 'metadata': {'escaped': '中"\\\n'}}
    raw['output'] = 'x' * (1048576 - len(json.dumps(raw).encode('utf-8')) + extra)
    original = ActionResult.from_runtime_result(ActionRequest('c', 'tool', {}), raw)
    processed = ActionRuntime.prepare_result(original)
    assert processed.success is True
    assert processed.metadata['result_processing']['raw_json_bytes'] == 1048576 + extra
    assert len(json.dumps(processed.raw).encode('utf-8')) <= 1048576
    assert processed.metadata['result_processing']['raw_available'] is (extra == 0)
    if not extra:
        assert processed.raw == raw


@pytest.mark.parametrize('output', ['中' * 100000, '\U0001f600' * 100000, {'items': ['"\\\n' * 50000]}], ids=['chinese', 'emoji', 'escaped'])
def test_projection_is_bounded_and_reports_integrity(output):
    import json

    raw = {'success': True, 'output': output}
    result = ActionRuntime.prepare_result(ActionResult.from_runtime_result(ActionRequest('c', 't', {}), raw))
    observation = result.to_observation(receipt_ref='agent-tool-result:op')
    assert len(observation) <= 50000
    assert len(observation.encode('utf-8')) <= 200000
    details = json.loads(observation)['result_processing']
    assert details['truncated'] is True
    assert details['returned_chars'] == len(observation)
    assert details['returned_bytes'] == len(observation.encode('utf-8'))
    assert 'agent-tool-result:op' in observation


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'exception', 'empty'])
async def test_summary_fallback_preserves_original_result(failure):
    from types import SimpleNamespace

    class Model:
        async def generate(self, **kwargs):
            if failure == 'exception':
                raise RuntimeError('summary failed')
            return SimpleNamespace(content='')

    prompts = SimpleNamespace(get_tool_summary_hint=lambda _: '', get_tool_summary_template=lambda: '{tool_name} {max_chars}')
    runtime = ActionRuntime(resource_pool=None if failure == 'missing' else Model(), prompts=prompts,
                            tool_result_summary_threshold=10, tool_result_summary_max_chars=20)
    raw = {'success': False, 'output': '中' * 100, 'error': 'original failure', 'metadata': {}}
    result = await runtime._summarize_if_needed(raw, 'tool')
    assert result['output'] == '中' * 100
    assert result['success'] is False and result['error'] == 'original failure'
    assert result['metadata']['result_processing']['summary_status'] != 'summarized'
    assert len(result['summary']) <= 20


def test_processing_failure_keeps_receipt_and_tool_outcome():
    import json

    raw = {'success': True, 'output': {'command_receipt': {'command_id': 'real-command'},
                                      'file_id': 'real-existing-file', 'body': object()}}
    result = ActionRuntime.prepare_result(ActionResult.from_runtime_result(ActionRequest('c', 't', {}), raw))
    assert result.success is True
    observation = json.loads(result.to_observation())
    assert observation['references']['command_receipt']['command_id'] == 'real-command'
    assert observation['references']['file_id'] == 'real-existing-file'
    assert observation['result_processing']['raw_available'] is False
    assert observation['result_processing']['processing_error']


async def test_registry_and_sandbox_preserve_structured_and_long_results():
    from types import SimpleNamespace

    from sandbox.sandbox import Sandbox
    from sandbox.tools.registry import ToolRegistry
    from sandbox.tools.types import ToolResult

    structured = {'body': 'x' * 60000}
    registry = ToolRegistry()
    async def invoke(_):
        return structured
    registry.get = lambda _: SimpleNamespace(ainvoke=invoke, metadata={})
    result = await registry.execute('t', {})
    assert result.output is structured
    registry.get_category = lambda _: 'native'
    original = ToolResult(output='x' * 60000)
    async def execute(*_):
        return original
    registry.execute = execute
    returned, _ = await Sandbox('/tmp', registry).execute('t', {})
    assert returned is original and len(original.output) == 60000


def test_untrusted_processing_metadata_cannot_forge_raw_reference():
    raw = {'success': True, 'output': 'x' * 1048576,
           'metadata': {'result_processing': {'raw_file_id': 'forged', 'raw_location': 'file'}}}
    result = ActionRuntime.prepare_result(ActionResult.from_runtime_result(ActionRequest('c', 't', {}), raw))
    assert result.metadata['result_processing']['raw_available'] is False
    assert 'raw_file_id' not in result.to_observation()


def test_huge_reference_fields_do_not_break_observation_bound():
    import json

    raw = {'success': True, 'output': {key: 'x' * 11900 for key in
           ('file_id', 'file_ids', 'outputs', 'result_ref', 'result_refs', 'artifact_id')}}
    result = ActionRuntime.prepare_result(ActionResult.from_runtime_result(ActionRequest('c', 't', {}), raw))
    from actions.contracts import result_references
    assert len(json.dumps(result_references(raw['output']))) <= 12000
    # Reference JSON is escaped by the same streaming encoder, with an aggregate cap.
    observation = result.to_observation()
    assert len(observation) <= 50000 and len(observation.encode()) <= 200000
    assert json.loads(observation)['result_processing']['raw_available'] is True


async def test_summary_success_retains_original_and_model_budget_context():
    from types import SimpleNamespace

    from resources.model_budget_context import current_model_budget, model_budget_scope

    class Model:
        async def generate(self, **kwargs):
            context = current_model_budget()
            assert context.phase == 'tool_result_summary'
            return SimpleNamespace(content='summary' * 10000)

    prompts = SimpleNamespace(get_tool_summary_hint=lambda _: '', get_tool_summary_template=lambda: '{tool_name} {max_chars}')
    runtime = ActionRuntime(resource_pool=Model(), prompts=prompts,
                            tool_result_summary_threshold=10, tool_result_summary_max_chars=20)
    raw = {'success': True, 'output': {'data': 'x' * 100}, 'metadata': {}}
    with model_budget_scope('account', 'run', 'operation', phase='tool_result_summary'):
        result = await runtime._summarize_if_needed(raw, 'tool')
    assert result['output'] == {'data': 'x' * 100}
    assert result['summary'] == ('summary' * 10000)[:20]
    assert result['metadata']['result_processing']['summary_status'] == 'summarized'


@pytest.mark.parametrize('field', ['output', 'metadata', 'error'])
def test_processing_structure_failure_is_reported_without_changing_outcome(field):
    import json

    loop = {}
    loop['cycle'] = loop
    raw = {'success': False, 'output': 'known output', 'error': 'original error', 'metadata': {}}
    raw[field] = loop
    result = ActionRuntime.prepare_result(ActionResult.from_runtime_result(ActionRequest('c', 't', {}), raw))
    assert result.failed is True
    assert result.output is raw['output'] and result.error is raw['error']
    observation = json.loads(result.to_observation())
    assert observation['result_processing']['raw_available'] is False
    assert observation['result_processing']['processing_error']


def test_processing_does_not_trust_tool_supplied_summary_status():
    import json

    raw = {'success': True, 'output': 'ok', 'metadata': {'result_processing': {'summary_status': ['bad']}}}
    result = ActionRuntime.prepare_result(ActionResult.from_runtime_result(ActionRequest('c', 't', {}), raw))
    assert json.loads(result.to_observation())['result_processing']['summary_status'] == 'not_requested'


def test_large_result_preserves_main_receipt_shape_and_budget_identity():
    import json

    output = json.dumps({'body': 'x' * 1048576, 'command_receipt': {'command_id': 'real-command'}})
    raw = {'success': True, 'output': output, 'metadata': {'invocation_key': 'invocation',
           'idempotency_key': 'operation', 'budget_usage': {'tool_calls': 1}}}
    result = ActionRuntime.prepare_result(ActionResult.from_runtime_result(ActionRequest('c', 'accept_work', {}), raw))
    # Main's authoritative respond_directly consumer reads this existing shape.
    assert result.raw['output']['command_receipt']['command_id'] == 'real-command'
    assert result.raw['metadata']['budget_usage'] == {'tool_calls': 1}
    assert result.raw['metadata']['idempotency_key'] == 'operation'


async def test_tool_processing_metadata_cannot_break_summary_after_execution():
    runtime = ActionRuntime(tool_result_summary_threshold=10)
    raw = {'success': True, 'output': 'x' * 100, 'metadata': {'result_processing': ['tool-owned-value']}}
    result = await runtime._summarize_if_needed(raw, 'tool')
    assert result['success'] is True and result['output'] == 'x' * 100
    assert result['metadata']['result_processing']['summary_status'] == 'preview_no_model'
