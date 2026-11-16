"""Ordinary tool registration owns metadata independently of sandbox wiring."""

from contextlib import AsyncExitStack

import pytest
from langchain_core.tools import StructuredTool

from sandbox.tools.local import LOCAL_TOOL_FACTORIES
from sandbox.tools.registry import ToolRegistry
from sandbox.tools.routing.models import ToolRoutingSpec


@pytest.fixture
async def resource_stack():
    async with AsyncExitStack() as stack:
        yield stack


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
        schema = next(t['function']['parameters'] for t in tools.tool_schemas if t['function']['name'] == 'fs_read')
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


def test_all_native_contracts_have_real_factories_and_valid_schemas(tmp_path, monkeypatch):
    from uuid import uuid4

    from jsonschema import Draft202012Validator

    from application.main_agent import create_main_work_tools
    from sandbox.tools.local.file_analysis import create_file_analysis_tools
    from sandbox.tools.local.file_read import create_file_read_tools
    from sandbox.tools.local.file_write import create_file_write_tools
    from sandbox.tools.local.run_candidates import create_run_candidate_tools
    from sandbox.tools.routing.contracts import NATIVE_ROUTING_SPECS, routing_for

    monkeypatch.delenv('FILE_DIRECT_READ_MAX_BYTES', raising=False)
    # Creating real tool wrappers invokes no command, file operation or service.
    context = {key: str(uuid4()) for key in ('account_id', 'conversation_id', 'trigger_message_id')}
    tools = [factory(str(tmp_path)) for factory in LOCAL_TOOL_FACTORIES.values()]
    tools += create_main_work_tools(context, object())
    tools += create_run_candidate_tools(lambda: None) + create_file_analysis_tools(lambda: None)
    tools += create_file_read_tools(lambda: None)
    tools += create_file_write_tools(lambda: None, object(), object())
    assert len({tool.name for tool in tools}) == len(tools)
    assert {tool.name for tool in tools} == set(NATIVE_ROUTING_SPECS)
    from dataclasses import asdict

    from bootstrap.worker_dependencies import build_tool_catalog

    definitions = {item['name']: item for item in build_tool_catalog(work_commands=object()).snapshot()}
    registry = ToolRegistry()
    for tool in tools:
        definition = definitions[tool.name]
        assert definition['description'] == tool.description
        assert definition['metadata'] == tool.metadata
        expected_routing = {key: sorted(value) if isinstance(value, frozenset) else value
                            for key, value in asdict(routing_for(tool, 'native')).items()}
        assert definition['routing'] == expected_routing
        schema = tool.args_schema if isinstance(tool.args_schema, dict) else tool.args_schema.model_json_schema()
        assert definition['parameters'] == schema
        Draft202012Validator.check_schema(schema)
        registry.register(tool, routing=routing_for(tool, 'native'))


@pytest.mark.parametrize('skill_type', ['pipeline', None, 'unknown'])
def test_current_skill_builder_rejects_non_instruction_without_executing(skill_type):
    from sandbox.tools.skills.engine import build_skill_tool_from_definition

    definition = {'name': 'unsafe', 'pipeline': {'steps': [{'id': 'write', 'tool': 'Bash'}]}}
    if skill_type is not None:
        definition['type'] = skill_type
    with pytest.raises(ValueError, match='instruction'):
        build_skill_tool_from_definition(definition, ToolRegistry())


@pytest.mark.parametrize('pipeline', [
    {'steps': [{'id': 'write', 'tool': 'Bash'}]}, {'steps': []}, None,
])
async def test_worker_loads_only_instruction_skills(resource_stack, tmp_path, pipeline, caplog):
    from bootstrap.worker_dependencies import setup_tools
    from orchestration.config import AppConfig
    from sandbox.tools.skills.engine import build_skill_tool_from_definition

    (tmp_path / 'pipeline.yaml').write_text('name: unsafe\npipeline:\n  steps: []\n')
    (tmp_path / 'instruction.yaml').write_text(
        'name: yaml_advice\ntype: instruction\ndescription: advice\nbody: YAML guidance\n')
    folder = tmp_path / 'markdown-advice'
    folder.mkdir()
    (folder / 'SKILL.md').write_text('---\nname: markdown-advice\ndescription: advice\n---\nMD guidance\n')
    import yaml

    unsafe = tmp_path / 'markdown-pipeline'
    unsafe.mkdir()
    (unsafe / 'SKILL.md').write_text('---\n' + yaml.safe_dump({
        'name': 'markdown-pipeline', 'description': 'unsafe', 'pipeline': pipeline,
    }) + '---\nunsafe guidance\n')
    config = AppConfig()
    config.models.tool_rag.enabled = False
    config.models.mcp.auto_connect = False
    config.models.skills.enabled = True
    config.models.skills.config_path = str(tmp_path)
    _, definitions, _ = await setup_tools(config, resource_stack=resource_stack)
    assert {d['name'] for d in definitions} == {'yaml_advice', 'markdown-advice'}
    assert 'Skills loading failed' not in caplog.text
    for definition in definitions:
        result = await build_skill_tool_from_definition(definition, ToolRegistry()).ainvoke({})
        assert result.success
        assert 'guidance' in result.output


async def test_manager_registers_instruction_and_never_dispatches_pipeline(tmp_path):
    from sandbox.sandbox_manager import SandboxManager

    manager = SandboxManager(nsjail_enabled=False, native_tools_enabled=False, skill_definitions=[
        {'name': 'unsafe', 'type': 'pipeline', 'pipeline': {'steps': []}},
        {'name': 'legacy_pipeline', 'pipeline': {'steps': []}},
        {'name': 'advice', 'type': 'instruction', 'description': 'advice', 'body': 'safe guidance'},
    ])
    try:
        sandbox_id = manager.create_execution_sandbox('skills', str(tmp_path))
        sandbox = manager.get_sandbox(sandbox_id)
        registry = sandbox._registry
        assert registry.get('unsafe') is None
        assert registry.get('legacy_pipeline') is None
        denied = await registry.execute('unsafe', {})
        assert not denied.success
        result = await registry.execute('advice', {})
        assert result.success and result.output == 'safe guidance'
    finally:
        manager.close()


def test_catalog_projects_real_factories_without_io_or_runtime_bindings(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from bootstrap.worker_dependencies import build_tool_catalog
    from sandbox.tools.routing.contracts import NATIVE_ROUTING_SPECS

    def forbidden(*args, **kwargs):
        raise AssertionError('declaration construction performed I/O')

    # Factories must only construct wrappers; supplied identities never reach declarations.
    with monkeypatch.context() as guard:
        guard.setattr(Path, 'read_text', forbidden)
        guard.setattr(Path, 'write_text', forbidden)
        guard.setattr('subprocess.run', forbidden)
        guard.setattr('socket.socket.connect', forbidden)
        catalog = build_tool_catalog(work_commands=object())
    definitions = catalog.snapshot()
    assert catalog.complete
    assert {d['name'] for d in definitions} == set(NATIVE_ROUTING_SPECS)
    serialized = json.dumps(definitions)
    assert '00000000-0000-0000-0000-000000000000' not in serialized
    assert 'coroutine' not in serialized and 'workspace_path' not in serialized
    read = next(d for d in definitions if d['name'] == 'fs_read')
    real = LOCAL_TOOL_FACTORIES['fs_read'](str(tmp_path))
    assert read['description'] == real.description
    assert read['parameters'] == real.args_schema.model_json_schema()
    assert read['metadata'] == real.metadata
    assert read['routing']['capability'] == 'workspace.read'
    read['parameters'].clear()
    assert next(d for d in catalog.snapshot() if d['name'] == 'fs_read')['parameters']


def test_catalog_rejects_duplicates_and_preserves_incomplete_sources():
    from sandbox.tools.registry import ToolDefinitionCatalog

    tool = LOCAL_TOOL_FACTORIES['fs_read']('/unused')
    catalog = ToolDefinitionCatalog()
    catalog.add(tool, category='native', routing=ToolRoutingSpec('workspace.read'))
    with pytest.raises(ValueError, match='duplicate'):
        catalog.add(tool, category='native', routing=ToolRoutingSpec('workspace.read'))
    catalog.mark_incomplete('mcp')
    assert not catalog.complete
    assert len(catalog.snapshot()) == 1


async def test_execution_lifecycle_never_reconciles_global_index(tmp_path):
    from sandbox.sandbox_manager import SandboxManager

    class Store:
        def sync(self, *args, **kwargs):
            raise AssertionError('Execution must not synchronize the global index')

    from types import SimpleNamespace
    retriever = SimpleNamespace(_store=Store(), _embedding=object())
    manager = SandboxManager(nsjail_enabled=False, retriever=retriever)
    try:
        # Old Manager swallowed sync errors; count actual calls as well.
        calls = []
        retriever._store.sync = lambda *args, **kwargs: calls.append(args)
        for key, context in [('root', {}), ('child', {'execution_role': 'subagent'})]:
            sid = manager.create_execution_sandbox(key, str(tmp_path), session_context=context)
            manager.destroy_sandbox(sid)
        assert calls == []
    finally:
        manager.close()


async def test_source_load_failure_marks_catalog_incomplete(resource_stack, tmp_path):
    from bootstrap.worker_dependencies import build_tool_catalog, setup_tools
    from orchestration.config import AppConfig

    (tmp_path / 'bad.yaml').write_text('[')
    config = AppConfig()
    config.models.tool_rag.enabled = False
    config.models.mcp.auto_connect = False
    config.models.skills.enabled = True
    config.models.skills.config_path = str(tmp_path)
    failures = set()
    manager, definitions, _ = await setup_tools(config, resource_stack=resource_stack, incomplete_sources=failures)
    catalog = build_tool_catalog(work_commands=object(), mcp_manager=manager,
                                 skill_definitions=definitions, incomplete_sources=failures)
    assert not catalog.complete
    assert any(d['name'] == 'fs_read' for d in catalog.snapshot())


async def test_enabled_rag_initialization_failure_is_not_silent_disable(resource_stack, monkeypatch):
    from bootstrap.worker_dependencies import setup_tools
    from orchestration.config import AppConfig
    from sandbox.tools.routing.models import ToolRetrievalError

    config = AppConfig()
    config.models.tool_rag.enabled = True
    def fail(*args, **kwargs):
        raise OSError('index unavailable')
    monkeypatch.setattr('sandbox.tools.retriever.ToolVectorStore', fail)
    with pytest.raises(ToolRetrievalError):
        await setup_tools(config, resource_stack=resource_stack)


def test_catalog_uses_connected_mcp_cache_and_instruction_declarations():
    from bootstrap.worker_dependencies import build_tool_catalog
    from sandbox.tools.skills.engine import build_skill_tool_from_definition

    mcp_tool = StructuredTool.from_function(lambda: 'not invoked', name='cached_lookup',
        description='Connected lookup', metadata={'server': 'existing', 'side_effect_class': 'read_only'})
    class Connected:
        def get_cached_tools(self):
            return [mcp_tool]
        def connect(self):
            raise AssertionError('catalog must not connect')
    instruction = {'type': 'instruction', 'name': 'guidance', 'description': 'Advice', 'body': 'Read first'}
    definitions = {item['name']: item for item in build_tool_catalog(work_commands=object(),
        mcp_manager=Connected(), skill_definitions=[instruction, {'type': 'pipeline'}]).snapshot()}
    assert definitions['cached_lookup']['parameters'] == mcp_tool.args_schema.model_json_schema()
    assert definitions['cached_lookup']['metadata'] == mcp_tool.metadata
    assert definitions['cached_lookup']['routing']['capability'] == 'mcp.existing.cached_lookup'
    actual = build_skill_tool_from_definition(instruction, None)
    assert definitions['guidance']['description'] == actual.description
    assert definitions['guidance']['parameters'] == actual.args_schema.model_json_schema()


@pytest.mark.parametrize('partial', [False, True])
async def test_swallowed_mcp_server_failure_cannot_make_catalog_complete(resource_stack, tmp_path, monkeypatch, partial):
    from bootstrap.worker_dependencies import setup_tools
    from orchestration.config import AppConfig
    from sandbox.tools.adapters.mcp import MCPToolManager

    path = tmp_path / 'servers.yaml'
    path.write_text('servers:\n  unavailable: {}\n  connected: {}\n  disabled:\n    disabled: true\n  omitted: null\n')
    config = AppConfig()
    config.models.tool_rag.enabled = False
    config.models.skills.enabled = False
    config.models.mcp.auto_connect = True
    config.models.mcp.config_path = str(path)
    class Session:
        async def list_tools(self):
            return []
        async def disconnect(self):
            pass
    async def connect(self, name, cfg):
        if partial and name == 'connected':
            return Session()
        raise OSError('unavailable')
    monkeypatch.setattr(MCPToolManager, '_connect_one', connect)
    failures = set()
    manager, _, _ = await setup_tools(config, resource_stack=resource_stack, incomplete_sources=failures)
    try:
        assert 'mcp' in failures
    finally:
        if manager is not None:
            await manager.disconnect()


@pytest.mark.parametrize('source', ['mcp', 'skills'])
async def test_missing_enabled_source_is_incomplete_not_an_empty_directory(resource_stack, tmp_path, source):
    from bootstrap.worker_dependencies import setup_tools
    from orchestration.config import AppConfig

    config = AppConfig()
    config.models.tool_rag.enabled = False
    config.models.skills.enabled = source == 'skills'
    config.models.skills.config_path = str(tmp_path / 'missing-skills')
    config.models.mcp.auto_connect = source == 'mcp'
    config.models.mcp.config_path = str(tmp_path / 'missing-mcp.yaml')
    failures = set()
    manager, _, _ = await setup_tools(config, resource_stack=resource_stack, incomplete_sources=failures)
    try:
        assert source in failures
    finally:
        if manager is not None:
            await manager.disconnect()


async def test_explicitly_disabled_mcp_servers_are_a_complete_source(resource_stack, tmp_path):
    from bootstrap.worker_dependencies import setup_tools
    from orchestration.config import AppConfig

    path = tmp_path / 'servers.yaml'
    path.write_text('servers:\n  disabled:\n    disabled: true\n  omitted: null\n')
    config = AppConfig()
    config.models.tool_rag.enabled = False
    config.models.skills.enabled = False
    config.models.mcp.auto_connect = True
    config.models.mcp.config_path = str(path)
    failures = set()
    manager, _, _ = await setup_tools(config, resource_stack=resource_stack, incomplete_sources=failures)
    try:
        assert not failures
    finally:
        if manager is not None:
            await manager.disconnect()


@pytest.mark.parametrize('finish', ['same_key', 'destroy', 'cleanup', 'close'])
def test_sandbox_lifecycle_serializes_creation_threads(tmp_path, monkeypatch, finish):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    import sandbox.sandbox_manager as module

    manager = module.SandboxManager(nsjail_enabled=False, max_idle_seconds=-1)
    entered, release, competing = Event(), Event(), Event()
    created = []
    real_sandbox = module.Sandbox

    def construct(**kwargs):
        sandbox = real_sandbox(**kwargs)
        created.append(sandbox)
        if len(created) == 1:
            entered.set()
            assert release.wait(5)
        return sandbox

    monkeypatch.setattr(module, 'Sandbox', construct)

    def compete():
        competing.set()
        if finish == 'same_key':
            return manager.create_execution_sandbox('execution:11', str(tmp_path))
        if finish == 'destroy':
            return manager.destroy_sandbox(created[0].sandbox_id)
        if finish == 'cleanup':
            return manager.cleanup_idle_sandboxes()
        return manager.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.create_execution_sandbox, 'execution:11', str(tmp_path))
        try:
            assert entered.wait(5)
            second = pool.submit(compete)
            assert competing.wait(5)
            # Construction is deliberately paused; lifecycle calls must wait.
            with pytest.raises(TimeoutError):
                second.result(timeout=0.05)
        finally:
            release.set()
        sandbox_id = first.result(timeout=5)
        outcome = second.result(timeout=5)
    if finish == 'same_key':
        assert outcome == sandbox_id
        assert len(created) == 1
        assert manager.get_sandbox_count() == 1
    else:
        assert created[0].status == 'destroyed'
        assert manager.get_sandbox_count() == 0
        if finish == 'destroy':
            assert outcome is True
        if finish == 'cleanup':
            assert outcome == 1
    manager.close()


async def test_sandbox_tasks_reuse_attempt_and_isolate_lease(tmp_path):
    import asyncio

    from sandbox.sandbox_manager import SandboxManager

    manager = SandboxManager(nsjail_enabled=False)

    async def create(key):
        await asyncio.sleep(0)
        return manager.create_execution_sandbox(key, str(tmp_path))

    same = await asyncio.gather(*(create('execution:11') for _ in range(8)))
    assert len(set(same)) == 1
    other = await create('execution:12')
    assert other != same[0]
    assert manager.get_sandbox(other)._registry is not manager.get_sandbox(same[0])._registry
    first = manager.get_sandbox(same[0])
    first._hints.append('first-attempt')
    assert manager.get_sandbox(other)._drain_hints() == []
    manager.destroy_sandbox(same[0])
    assert manager.get_sandbox_for_execution('execution:12').status == 'active'
    manager.close()
    with pytest.raises(RuntimeError, match='closed'):
        await create('execution:13')
    assert manager.get_sandbox_count() == 0


@pytest.mark.parametrize('failure_point', ['factory', 'after_construction'])
def test_sandbox_creation_failure_cleans_local_state_and_allows_retry(tmp_path, monkeypatch, failure_point):
    from types import SimpleNamespace

    import sandbox.sandbox_manager as module

    shared = SimpleNamespace(get_cached_tools=lambda: [], close=lambda: pytest.fail('shared close'))
    manager = module.SandboxManager(nsjail_enabled=False, mcp_manager=shared, retriever=shared)
    created = []
    real_sandbox = module.Sandbox

    def construct(**kwargs):
        sandbox = real_sandbox(**kwargs)
        created.append(sandbox)
        return sandbox

    def fail(*args, **kwargs):
        raise ValueError('injected creation failure')

    monkeypatch.setattr(module, 'Sandbox', construct)
    with monkeypatch.context() as patch:
        if failure_point == 'factory':
            patch.setitem(module.LOCAL_TOOL_FACTORIES, 'fs_read', fail)
        else:
            patch.setattr(module.ToolRegistry, 'count_by_category', fail)
        with pytest.raises(ValueError, match='injected'):
            manager.create_execution_sandbox('execution:11', str(tmp_path))
    assert manager.get_sandbox_count() == 0
    assert not manager._execution_to_sandbox
    assert all(s.status == 'destroyed' for s in created)
    sandbox_id = manager.create_execution_sandbox('execution:11', str(tmp_path))
    assert manager.get_sandbox_for_execution('execution:11').sandbox_id == sandbox_id
    manager.close()
    manager.close()


def test_sandbox_lifecycle_logs_safe_identity_and_failure_stack(tmp_path, monkeypatch, caplog):
    import json
    import logging

    import sandbox.sandbox_manager as module
    from common.logging import _JsonFormatter

    manager = module.SandboxManager(nsjail_enabled=False)
    caplog.set_level(logging.INFO)
    secret = 'credential-and-private-body'
    with monkeypatch.context() as patch:
        def fail(*args):
            raise ValueError(secret)
        patch.setitem(module.LOCAL_TOOL_FACTORIES, 'fs_read', fail)
        with pytest.raises(ValueError):
            manager.create_execution_sandbox('execution:11', str(tmp_path), user_uuid='account')
    sandbox_id = manager.create_execution_sandbox('execution:11', str(tmp_path), user_uuid='account')
    manager.close()
    records = [json.loads(_JsonFormatter().format(r)) for r in caplog.records
               if getattr(r, 'event', '').startswith('sandbox_')]
    failed = next(r for r in records if r['event'] == 'sandbox_creation_failed')
    assert failed['execution_id'] == 'execution'
    assert failed['lease_token'] == '11'
    assert failed['resource_key'] == 'execution:11'
    assert failed['account_id'] == 'account'
    assert failed['elapsed_ms'] >= 0
    assert failed['error_type'] == 'ValueError'
    assert failed['traceback']
    assert secret not in json.dumps(records)
    assert sum(r['event'] == 'sandbox_creation_failed' for r in records) == 1
    assert any(r.get('sandbox_id') == sandbox_id and r['event'] == 'sandbox_created' for r in records)
    assert any(r.get('sandbox_id') == sandbox_id and r['event'] == 'sandbox_destroyed' for r in records)


async def test_tool_exception_diagnostic_has_attempt_and_no_private_values(tmp_path, monkeypatch, caplog):
    import json
    import logging

    from actions.contracts import ActionRequest
    from actions.runtime import ActionRuntime
    from common.logging import _JsonFormatter
    from sandbox.sandbox_manager import SandboxManager

    def broken_tool(workspace):
        def invoke(path: str):
            raise ValueError('private-body-and-credential')
        tool = StructuredTool.from_function(invoke, name='fs_read', description='test')
        tool.metadata = {'side_effect_class': 'read_only'}
        return tool

    monkeypatch.setitem(LOCAL_TOOL_FACTORIES, 'fs_read', broken_tool)
    caplog.set_level(logging.INFO)
    manager = SandboxManager(nsjail_enabled=False)
    sandbox_id = manager.create_execution_sandbox('execution:11', str(tmp_path))
    result = await ActionRuntime(sandbox_manager=manager).execute_request(
        ActionRequest('call', 'fs_read', {'path': 'private-argument'}),
        resource_key='execution:11', execution_id='execution',
    )
    assert result.failed
    manager.close()
    records = [json.loads(_JsonFormatter().format(r)) for r in caplog.records]
    errors = [r for r in records if r.get('traceback')]
    assert len(errors) == 1
    assert errors[0]['execution_id'] == 'execution'
    assert errors[0]['resource_key'] == 'execution:11'
    assert errors[0]['sandbox_id'] == sandbox_id
    assert errors[0]['tool'] == 'fs_read'
    assert errors[0]['error_type'] == 'ValueError'
    assert 'private-body-and-credential' not in json.dumps(records)
    assert 'private-argument' not in json.dumps(records)
