"""Execution parameters come from immutable facts, never mutable worker defaults."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from run_domain.input import RunInputLoader


def identity(snapshot):
    return dict(status='running', source_kind='work', strategy_kind='generic_agent',
                conversation_id=None, work_id='work', account_id='account',
                execution_id='execution', agent_strategy='react', input_snapshot=snapshot)


def test_run_loader_uses_admitted_turn_limit_after_worker_config_change():
    frozen = {'schema_version': 1, 'max_tool_turns': 7, 'default_timezone': 'UTC',
              'tool_result_summary_enabled': False, 'tool_result_summary_threshold': 600,
              'tool_result_summary_max_chars': 120}
    loader = RunInputLoader(SimpleNamespace(run_identity=lambda _: identity({'agent_policy': frozen})), max_turns=99)
    assert loader.load('run').max_turns == 7


def test_legacy_run_loader_keeps_original_fallback_without_backfill():
    row = identity({})
    loader = RunInputLoader(SimpleNamespace(run_identity=lambda _: row), max_turns=9)
    assert loader.load('run').max_turns == 9
    assert row['input_snapshot'] == {}


@pytest.mark.parametrize('snapshot', [None, {}, {'schema_version': 2}])
def test_present_invalid_policy_never_falls_back(snapshot):
    loader = RunInputLoader(SimpleNamespace(run_identity=lambda _: identity({'agent_policy': snapshot})), max_turns=9)
    with pytest.raises(ValueError, match='agent_policy'):
        loader.load('run')


def test_policy_reads_standard_yaml_without_replacing_explicit_values(tmp_path):
    from common.agent_policy import AgentPolicy, load_agent_policy
    path = tmp_path / 'config.yaml'
    path.write_text('agent:\n  max_tool_turns: 5\n  default_timezone: UTC\n')
    policy = load_agent_policy(path)
    assert policy.max_tool_turns == 5
    assert policy.default_timezone == 'UTC'
    assert AgentPolicy().max_tool_turns == 20
    assert AgentPolicy.from_snapshot(policy.to_snapshot()) == policy


@pytest.mark.parametrize('field,value', [('max_tool_turns', True), ('max_tool_turns', -1),
    ('default_timezone', 'invalid-zone'), ('tool_result_summary_enabled', 'false'),
    ('tool_result_summary_threshold', 0), ('tool_result_summary_max_chars', -3)])
def test_policy_rejects_bad_types_ranges_and_timezone(field, value):
    from common.agent_policy import AgentPolicy
    with pytest.raises(ValueError, match='agent_policy.' + field):
        replace(AgentPolicy(), **{field: value})


@pytest.mark.asyncio
async def test_concurrent_tool_summaries_use_each_run_policy_without_mutating_runtime():
    import asyncio

    from actions.contracts import ActionRequest
    from actions.runtime import ActionRuntime
    from common.agent_policy import AgentPolicy

    class Sandbox:
        async def execute(self, name, arguments):
            await asyncio.sleep(0)
            return SimpleNamespace(to_dict=lambda: {'output': 'abcdefghij', 'success': True}), {}

    runtime = ActionRuntime(sandbox_manager=SimpleNamespace(get_sandbox_for_execution=lambda _: Sandbox()),
                            tool_result_summary_threshold=100, tool_result_summary_max_chars=90)
    async def call(policy):
        return await runtime.execute_request(ActionRequest('call', 'read', {}), resource_key='res',
            execution_id='execution', agent_policy=policy)
    short, untouched = await asyncio.gather(
        call(AgentPolicy(tool_result_summary_threshold=3, tool_result_summary_max_chars=4)),
        call(AgentPolicy(tool_result_summary_enabled=False)))
    assert short.output == 'abcd'
    assert untouched.output == 'abcdefghij'
    assert (await call(None)).output == 'abcdefghij'


@pytest.mark.parametrize('text', ['', 'agent: null\n'])
def test_optional_empty_agent_section_keeps_existing_code_defaults(tmp_path, text):
    from common.agent_policy import load_agent_policy
    path = tmp_path / 'config.yaml'
    path.write_text(text)
    assert load_agent_policy(path).max_tool_turns == 20
