"""Configuration contracts at load and composition boundaries, without services."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import orchestration.config as configuration
from application.prompts import PromptLoader
from bootstrap.shared_runtime import build_shared_agent_services
from memory.quality_config import MemoryConfigError
from orchestration.config import AppConfig, ModelsConfig
from web_api.config import WebApiSettings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_environment():
    with patch.dict(os.environ, {}, clear=True):
        yield


@pytest.mark.parametrize(("text", "field"), [
    ('agnet: {max_tool_turns: secret-value}', 'config.agnet'),
    ('agent: {max_tool_truns: secret-value}', 'agent.max_tool_truns'),
    ('redis: {default_ttl_seconds: secret-value}', 'redis.default_ttl_seconds'),
    ('channels: {onebot11: {ack_timout_seconds: secret-value}}',
     'channels.onebot11.ack_timout_seconds'),
])
def test_unknown_application_fields_fail_with_path_not_value(text, field, tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text(text)
    with pytest.raises(ValueError, match=field) as error:
        AppConfig.from_yaml(path)
    assert 'secret-value' not in str(error.value)


@pytest.mark.parametrize(("text", "field"), [
    ('chatt: [{model: secret-value}]', 'models.chatt'),
    ('providers: {demo: {api_fomat: secret-value}}', 'models.providers.demo.api_fomat'),
    ('models: {chatt: [{model: secret-value}]}', 'models.models.chatt'),
    ('tool_rag: {top_kk: secret-value}', 'models.tool_rag.top_kk'),
    ('mcp: {autoconnect: secret-value}', 'models.mcp.autoconnect'),
    ('skills: {enable: secret-value}', 'models.skills.enable'),
    ('rerank: {timout: secret-value}', 'models.rerank.timout'),
])
def test_unknown_model_settings_fail_with_path_not_value(text, field, tmp_path):
    path = tmp_path / 'models.yaml'
    path.write_text(text)
    with pytest.raises(ValueError, match=field) as error:
        ModelsConfig.from_yaml(path)
    assert 'secret-value' not in str(error.value)


def test_known_retired_application_fields_are_ignored_with_migration_warning(caplog):
    config = AppConfig._from_dict({
        'scheduler': {'enabled': False}, 'session': {'backup_dir': 'secret-value'},
        'agent': {'mode': 'multi', 'idle_timeout_minutes': 1, 'max_history_turns': 2,
                  'reflect_interval_hours': 2, 'max_tool_turns': 7},
        'workspace': {'db_path': 'secret-value'},
    })
    assert config.agent.max_tool_turns == 7
    assert config.workspace.root == '.data/workspace'
    for field in ('config.scheduler', 'config.session', 'agent.mode',
                  'agent.idle_timeout_minutes', 'agent.max_history_turns',
                  'agent.reflect_interval_hours', 'workspace.db_path'):
        assert field in caplog.text
    assert 'ignored' in caplog.text
    assert 'secret-value' not in caplog.text


def test_unloaded_research_section_warns_without_changing_policy(caplog):
    config = AppConfig._from_dict({'research': {'max_sources': 1, 'max_iterations': 1}})
    assert config.research.max_sources == 30
    assert config.research.max_iterations == 3
    assert 'config.research' in caplog.text and 'ignored' in caplog.text


def test_models_use_declared_defaults_and_keep_provider_extensions(tmp_path, monkeypatch):
    @dataclass
    class CustomProvider(configuration.ProviderEntry):
        api_format: str = 'openai'

    @dataclass
    class CustomModel(configuration.ModelEntry):
        max_tokens: int = 3210
        timeout: float = 17.5

    monkeypatch.setattr(configuration, 'ProviderEntry', CustomProvider)
    monkeypatch.setattr(configuration, 'ModelEntry', CustomModel)
    path = tmp_path / 'models.yaml'
    path.write_text('''
providers:
  demo:
    base_url: https://models.invalid/v1
    extra_body: {vendor_options: {region: fixture}}
models:
  chat: [{provider: demo, model: fixture, extra_body: {vendor_options: {thinking: false}}}]
''')
    models = ModelsConfig.from_yaml(path)
    endpoint = models.resolve_endpoint(models.chat[0])
    assert endpoint.extra['api_format'] == 'openai'
    assert endpoint.extra['max_tokens'] == 3210
    assert endpoint.extra['timeout'] == 17.5
    assert endpoint.extra['extra_body'] == {'vendor_options': {'region': 'fixture', 'thinking': False}}


def test_api_environment_loader_uses_declared_settings_defaults(monkeypatch):
    @dataclass(frozen=True)
    class CustomSettings(WebApiSettings):
        file_max_bytes: int = 123456
        file_max_count_per_message: int = 3
        sse_max_connections: int = 17
        cookie_secure: bool = False

    monkeypatch.setenv('APP_DATABASE_URL', 'postgresql://unused')
    settings = CustomSettings.from_env()
    assert settings.file_max_bytes == 123456
    assert settings.file_max_count_per_message == 3
    assert settings.sse_max_connections == 17
    assert settings.cookie_secure is False
    monkeypatch.setenv('FILE_MAX_BYTES', '654321')
    assert CustomSettings.from_env().file_max_bytes == 654321


def test_api_byte_secret_default_does_not_require_utf8(monkeypatch):
    @dataclass(frozen=True)
    class CustomSettings(WebApiSettings):
        qq_binding_code_pepper: bytes = b'\x80' * 32

    monkeypatch.setenv('APP_DATABASE_URL', 'postgresql://unused')
    assert CustomSettings.from_env().qq_binding_code_pepper == b'\x80' * 32


def test_moved_memory_prompt_still_reports_its_migration_path(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('hindsight: {retain_mission: secret-value}\n')
    with pytest.raises(MemoryConfigError, match='hindsight.retain_mission.*prompts/memory.yaml') as error:
        AppConfig.from_yaml(path)
    assert 'secret-value' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(("rag", "expected"), [('', 8), ('tool_rag: {top_k: 37}', 37)])
async def test_existing_yaml_tool_limit_reaches_selection_without_rerank_override(tmp_path, rag, expected):
    path = tmp_path / 'models.yaml'
    path.write_text(rag + '\nrerank: {top_n: 1}\n')
    config = AppConfig(models=ModelsConfig.from_yaml(path))

    class ToolSandbox:
        async def select_tools(self, query, limit):
            return [{'name': f'tool-{index}'} for index in range(limit)], {}

    manager = SimpleNamespace(get_sandbox_for_execution=lambda _: ToolSandbox())
    shared = build_shared_agent_services(
        config=config, worker_database_url='postgresql://unused', sandbox_manager=manager,
        resource_pool=SimpleNamespace(model=None), hindsight_client=None,
        prompt_loader=PromptLoader(config.prompts),
    )
    selected = await shared.action_runtime.select_tools(
        user_content='query', resource_key='scratch', execution_id='execution',
    )
    assert len(selected) == expected


def test_nested_model_form_keeps_its_existing_precedence(tmp_path, caplog):
    path = tmp_path / 'models.yaml'
    path.write_text('chat: [{model: shadow}]\nmodels: {chat: [{model: selected}]}\n')
    models = ModelsConfig.from_yaml(path)
    assert [entry.model for entry in models.chat] == ['selected']
    assert 'takes precedence' in caplog.text


@pytest.mark.asyncio
async def test_retired_channel_overrides_do_not_change_formal_brain_requests(tmp_path, caplog):
    path = tmp_path / 'models.yaml'
    path.write_text('''
chat: [{model: fixture, max_tokens: 4096, timeout: 30}]
channel_overrides: {web: {max_tokens: 1, timeout: 1, stream: true}}
rerank: {top_n: 1}
''')
    config = AppConfig(models=ModelsConfig.from_yaml(path))

    class ModelBoundary:
        async def generate(self, **request):
            return request

    shared = build_shared_agent_services(
        config=config, worker_database_url='postgresql://unused', sandbox_manager=None,
        resource_pool=SimpleNamespace(model=ModelBoundary()), hindsight_client=None,
        prompt_loader=PromptLoader(config.prompts),
    )
    request = await shared.brain_engine.generate_chat(messages=[{'role': 'user', 'content': 'hello'}])
    assert request['max_tokens'] is None
    assert request['latency_budget'] is None
    assert request['stream'] is False
    assert 'models.channel_overrides' in caplog.text and 'models.rerank.top_n' in caplog.text
    assert 'ignored' in caplog.text


def test_canonical_files_do_not_emit_retired_field_warnings(caplog):
    AppConfig.from_yaml(ROOT / 'config/config.yaml')
    assert 'ignored' not in caplog.text
    assert 'Unknown keys' not in caplog.text
