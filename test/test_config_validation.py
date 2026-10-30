"""Unsafe inputs fail at configuration boundaries before runtime construction."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from memory.quality_config import MemoryQualityConfig
from orchestration.config import AppConfig, ModelsConfig, PromptsConfig


@pytest.fixture(autouse=True)
def isolated_environment():
    with patch.dict(os.environ, {}, clear=True):
        yield


def write_config(tmp_path, text, filename="config.yaml"):
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(("text", "field"), [
    ('agent: {max_tool_turns: "secret-invalid"}', "agent.max_tool_turns"),
    ('agent: {tool_result_summary_enabled: "false"}', "agent.tool_result_summary_enabled"),
    ('agent: {max_tool_turns: true}', "agent.max_tool_turns"),
    ('agent: {max_tool_turns: 0}', "agent.max_tool_turns"),
    ('agent: {max_tool_turns: -1}', "agent.max_tool_turns"),
    ('agent: {default_timezone: "secret-invalid-zone"}', "agent.default_timezone"),
    ('hindsight: {recall_timeout: .nan}', "hindsight.recall_timeout"),
    ('hindsight: {recall_timeout: ' + str(10 ** 400) + '}', "hindsight.recall_timeout"),
    ('hindsight: {retain_timeout: -1}', "hindsight.retain_timeout"),
    ('redis: {group_context_density_threshold: .inf}', "redis.group_context_density_threshold"),
    ('channels: {enabled: "secret-invalid"}', "channels.enabled"),
    ('channels: {onebot11: {port: "secret-invalid"}}', "channels.onebot11.port"),
    ('channels: {onebot11: {ack_timeout_seconds: -1}}', "channels.onebot11.ack_timeout_seconds"),
    ('workspace: {prefork_enabled: "false"}', "workspace.prefork_enabled"),
    ('agent: []', "agent"),
    ('[]', "config"),
    ('false', "config"),
])
def test_application_rejects_invalid_values_with_safe_field_paths(tmp_path, text, field):
    with pytest.raises(ValueError, match=field) as error:
        AppConfig.from_yaml(write_config(tmp_path, text))
    assert "secret-invalid" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(("name", "value"), [
    ("AGENT_EXECUTION_LEASE_TTL_SECONDS", "secret-invalid-number"),
    ("AGENT_EXECUTION_LEASE_TTL_SECONDS", "-1"),
    ("WEB_OUTBOX_RECOVERY_INTERVAL_SECONDS", "-1"),
    ("HPAGENT_ENV", "secret-invalid-environment"),
])
def test_environment_overrides_are_revalidated_and_value_safe(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=f"{name}|temporal") as error:
        AppConfig.from_yaml(write_config(tmp_path, '{}'))
    assert "secret-invalid" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(("text", "field"), [
    ('chat: [{model: demo, timeout: -1}]', 'models.chat.0.timeout'),
    ('chat: [{model: demo, timeout: .inf}]', 'models.chat.0.timeout'),
    ('chat: [{model: demo, max_tokens: false}]', 'models.chat.0.max_tokens'),
    ('chat: [{model: demo, model: secret-invalid}]', 'models.chat.0.model'),
    ('chat: [secret-invalid]', 'models.chat.0'),
    ('chat: [null]', 'models.chat.0'),
    ('chat: {model: secret-invalid}', 'models.chat'),
    ('providers: {demo: {api_format: secret-invalid}}', 'models.providers.demo.api_format'),
    ('providers: {demo: {base_url: 123}}', 'models.providers.demo.base_url'),
    ('tool_rag: {enabled: "false"}', 'models.tool_rag.enabled'),
    ('tool_rag: {top_k: -1}', 'models.tool_rag.top_k'),
    ('channel_overrides: {web: {timeout: -1}}', 'models.channel_overrides.web.timeout'),
    ('rerank: {timeout: -1}', 'models.rerank.timeout'),
    ('providers: [secret-invalid]', 'models.providers'),
    ('providers: {demo: null}', 'models.providers.demo'),
    ('channel_overrides: {web: null}', 'models.channel_overrides.web'),
])
def test_models_reject_invalid_structure_types_and_bounds(tmp_path, text, field):
    with pytest.raises(ValueError, match=field) as error:
        ModelsConfig.from_yaml(write_config(tmp_path, text, 'models.yaml'))
    assert 'secret-invalid' not in str(error.value)


@pytest.mark.parametrize("kind", ['application', 'models', 'quality', 'prompt'])
def test_duplicate_yaml_keys_are_rejected_before_last_value_wins(tmp_path, kind):
    if kind == 'application':
        load = lambda: AppConfig.from_yaml(write_config(
            tmp_path, 'agent:\n  max_tool_turns: 4\n  max_tool_turns: 7\n'))
        field = 'agent.max_tool_turns'
    elif kind == 'models':
        load = lambda: ModelsConfig.from_yaml(write_config(
            tmp_path, 'providers:\n  demo:\n    api_key: first\n    api_key: secret-invalid\n'))
        field = 'providers.demo.api_key'
    elif kind == 'quality':
        load = lambda: MemoryQualityConfig.from_yaml(write_config(
            tmp_path, 'recall:\n  top_n: 4\n  top_n: 7\n'))
        field = 'recall.top_n'
    else:
        write_config(tmp_path, 'guide: first\nguide: secret-invalid\n', 'guidance.yaml')
        load = lambda: PromptsConfig.from_dir(tmp_path)
        field = 'guide'
    with pytest.raises(ValueError, match=field) as error:
        load()
    assert 'secret-invalid' not in str(error.value)


def test_yaml_parser_errors_do_not_echo_lines_or_values(tmp_path):
    with pytest.raises(ValueError, match='config') as error:
        AppConfig.from_yaml(write_config(tmp_path, 'hindsight: {api_key: secret-invalid\n'))
    assert 'secret-invalid' not in str(error.value)
    assert error.value.__cause__ is None


def test_valid_yaml_merges_overrides_and_model_payload_are_preserved(tmp_path):
    path = write_config(tmp_path, '''
providers:
  demo: {base_url: 'https://models.invalid/v1', api_format: openai}
chat:
  - <<: &defaults {provider: demo, model: base, timeout: 17.5, max_tokens: 3000}
    model: selected
    extra_body: {enable_thinking: false}
''', 'models.yaml')
    endpoint = ModelsConfig.from_yaml(path).resolve_endpoint(ModelsConfig.from_yaml(path).chat[0])
    assert endpoint.model == 'selected'
    assert endpoint.extra['timeout'] == 17.5
    assert endpoint.extra['max_tokens'] == 3000
    assert endpoint.extra['extra_body'] == {'enable_thinking': False}


def test_environment_override_keeps_existing_precedence_and_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('WORKSPACE_ROOT', '/tmp/explicit-workspace')
    monkeypatch.setenv('WEB_OUTBOX_LEASE_TIMEOUT_SECONDS', '90')
    monkeypatch.setenv('WEB_OUTBOX_RECOVERY_INTERVAL_SECONDS', '20')
    config = AppConfig.from_yaml(write_config(tmp_path, '''
workspace: {root: relative-workspace}
agent: {max_tool_turns: 7}
'''))
    assert config.workspace.root == '/tmp/explicit-workspace'
    assert config.temporal.web_outbox_lease_timeout_seconds == 90
    assert config.temporal.web_outbox_recovery_interval_seconds == 20
    assert config.agent.max_tool_turns == 7


def test_memory_configuration_still_loads_as_a_standalone_service_module(tmp_path):
    path = write_config(tmp_path, 'recall: {top_n: 7}')
    source = Path(__file__).resolve().parents[1] / 'src/memory/quality_config.py'
    result = subprocess.run([
        sys.executable, '-I', '-c',
        'import runpy, sys; module = runpy.run_path(sys.argv[1]); '
        'assert module["MemoryQualityConfig"].from_yaml(sys.argv[2]).recall.top_n == 7',
        str(source), str(path),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
