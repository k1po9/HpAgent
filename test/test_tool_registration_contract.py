"""Ordinary tool registration owns metadata independently of sandbox wiring."""

import pytest
from langchain_core.tools import StructuredTool

from sandbox.tools.local import LOCAL_TOOL_FACTORIES
from sandbox.tools.registry import ToolRegistry
from sandbox.tools.routing.models import ToolRoutingSpec


async def test_fs_read_uses_registration_discovery_schema_and_action_result(tmp_path):
    from actions.contracts import ActionRequest
    from actions.runtime import ActionRuntime
    from sandbox.sandbox_manager import SandboxManager

    (tmp_path / 'source.txt').write_text('bounded source\nsecond line', encoding='utf-8')
    manager = SandboxManager(nsjail_enabled=False)
    sandbox_id = manager.create_execution_sandbox('tool-test', str(tmp_path))
    runtime = ActionRuntime(sandbox_manager=manager)
    try:
        tools = await runtime.select_tools(resource_key='tool-test', execution_id='root',
                                           user_content='Read source.txt')
        schema = next(t['function']['parameters'] for t in tools if t['function']['name'] == 'fs_read')
        assert schema['properties']['path']['type'] == 'string'
        assert runtime.side_effect_class('tool-test', 'fs_read') == 'read_only'
        assert runtime.budget_reservation('tool-test', 'fs_read') == {'tool_calls': 1}
        result = await runtime.execute_request(
            ActionRequest('read', 'fs_read', {'path': 'source.txt', 'limit': 1}),
            resource_key='tool-test', execution_id='root', idempotency_key='stable-read',
        )
        assert result.success and '1\tbounded source' in result.output
        assert 'second line' not in result.output
        invalid = await runtime.execute_request(
            ActionRequest('bad', 'fs_read', {'path': 'source.txt', 'limit': 2001}),
            resource_key='tool-test', execution_id='root',
        )
        assert not invalid.success
    finally:
        manager.destroy_sandbox(sandbox_id)
        manager.close()


@pytest.mark.parametrize('name,effect', [
    ('fs_read', 'read_only'), ('Glob', 'read_only'), ('Grep', 'read_only'),
    ('fs_write', 'idempotent_write'), ('fs_edit', 'non_idempotent_write'),
    ('Bash', 'non_idempotent_write'),
])
def test_local_factory_declares_its_effect_without_manager_patch(tmp_path, name, effect):
    tool = LOCAL_TOOL_FACTORIES[name](str(tmp_path))
    assert tool.metadata['side_effect_class'] == effect


@pytest.mark.parametrize('name', ['capability__html__v1', 'result__save'])
@pytest.mark.parametrize('category', ['native', 'mcp', 'skill'])
def test_ordinary_registry_cannot_shadow_harness_protocol(name, category):
    tool = StructuredTool.from_function(lambda: 'unexpected', name=name, description='test')
    with pytest.raises(ValueError, match='reserved'):
        ToolRegistry().register(tool, category=category, routing=ToolRoutingSpec('test.shadow'))
