"""模型配置解析、请求参数透传和端点实例隔离测试。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from orchestration.config import ModelsConfig
from resources.credentials import CredentialManager, ModelEndpoint
from resources.model_client import ModelClient
from resources.resource_pool import ResourcePool


def test_model_extra_body_overrides_provider_defaults(tmp_path):
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
providers:
  minimax:
    base_url: https://example.test/v1
    api_key: test-key
    api_format: openai
    extra_body:
      thinking:
        type: adaptive
      vendor_options:
        trace: false
        region: default
chat:
  - provider: minimax
    model: MiniMax-M3
    max_tokens: 4096
    timeout: 30
    extra_body:
      thinking:
        type: disabled
      vendor_options:
        region: cn
""",
        encoding="utf-8",
    )

    config = ModelsConfig.from_yaml(config_path)
    entry = config.chat[0]
    endpoint = config.resolve_endpoint(entry, endpoint_id="chat:0:minimax:MiniMax-M3")

    assert entry.extra_body == {
        "thinking": {"type": "disabled"},
        "vendor_options": {"region": "cn"},
    }
    assert endpoint.endpoint_id == "chat:0:minimax:MiniMax-M3"
    assert endpoint.extra["extra_body"] == {
        "thinking": {"type": "disabled"},
        "vendor_options": {"trace": False, "region": "cn"},
    }


def test_misplaced_provider_request_field_fails_fast(tmp_path):
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
providers:
  minimax:
    base_url: https://example.test/v1
    api_key: test-key
    api_format: openai
chat:
  - provider: minimax
    model: MiniMax-M3
    thinking:
      type: disabled
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be nested under extra_body"):
        ModelsConfig.from_yaml(config_path)


def test_model_client_sends_extra_body_without_overriding_standard_fields():
    client = ModelClient(
        {
            "endpoint_id": "chat:0:minimax:MiniMax-M3",
            "provider": "minimax",
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "model": "MiniMax-M3",
            "api_format": "openai",
            "max_tokens": 4096,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    )

    payload = client._build_payload(
        [{"role": "user", "content": "hello"}],
        tools=None,
        stream=False,
    )

    assert payload["model"] == "MiniMax-M3"
    assert payload["max_tokens"] == 4096
    assert payload["thinking"] == {"type": "disabled"}


def test_model_client_rejects_reserved_extra_body_fields():
    with pytest.raises(ValueError, match="cannot override standard request fields"):
        ModelClient(
            {
                "api_key": "test-key",
                "base_url": "https://example.test/v1",
                "model": "MiniMax-M3",
                "extra_body": {"model": "unexpected-model"},
            }
        )


@pytest.mark.asyncio
async def test_same_remote_model_keeps_independent_endpoint_configs():
    manager = CredentialManager()
    manager.register_model_chain(
        [
            ModelEndpoint(
                endpoint_id="fast:0:minimax:MiniMax-M3",
                provider="minimax",
                api_key="test-key",
                base_url="https://example.test/v1",
                model="MiniMax-M3",
                extra={
                    "api_format": "openai",
                    "max_tokens": 1024,
                    "timeout": 15.0,
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
            ),
            ModelEndpoint(
                endpoint_id="reasoning:0:minimax:MiniMax-M3",
                provider="minimax",
                api_key="test-key",
                base_url="https://example.test/v1",
                model="MiniMax-M3",
                extra={
                    "api_format": "openai",
                    "max_tokens": 4096,
                    "timeout": 60.0,
                    "extra_body": {"thinking": {"type": "adaptive"}},
                },
            ),
        ]
    )

    pool = ResourcePool(manager)
    await pool.initialize_models()

    assert set(pool._model_clients) == {
        "fast:0:minimax:MiniMax-M3",
        "reasoning:0:minimax:MiniMax-M3",
    }
    fast_client = pool._model_clients["fast:0:minimax:MiniMax-M3"]["client"]
    reasoning_client = pool._model_clients[
        "reasoning:0:minimax:MiniMax-M3"
    ]["client"]
    assert fast_client._max_tokens == 1024
    assert fast_client._timeout == 15.0
    assert fast_client._extra_body["thinking"]["type"] == "disabled"
    assert reasoning_client._max_tokens == 4096
    assert reasoning_client._timeout == 60.0
    assert reasoning_client._extra_body["thinking"]["type"] == "adaptive"
