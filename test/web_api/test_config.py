from __future__ import annotations

import subprocess
import sys

import pytest

from web_api.config import WebApiSettings


def _settings(**overrides):
    values = {
        "database_url": "postgresql://unused",
        "worker_database_url": "postgresql://unused-worker",
        "public_origin": "https://example.test",
        "cursor_signing_keys": {"v1": b"x" * 32},
        "active_cursor_key_id": "v1",
        "session_token_pepper": b"y" * 32,
        "csrf_signing_key": b"z" * 32,
        "environment": "test",
    }
    values.update(overrides)
    return WebApiSettings(**values)


def test_fake_executor_is_rejected_in_production():
    with pytest.raises(ValueError, match="fake executor"):
        _settings(environment="production", fake_executor_enabled=True)


def test_production_rejects_short_secrets():
    with pytest.raises(ValueError, match="32 bytes"):
        _settings(environment="production", csrf_signing_key=b"short")


def test_web_api_import_does_not_load_agent_runtime():
    code = (
        "import sys; import web_api.app; "
        "forbidden=('agent','sandbox','temporalio','channels.router'); "
        "assert not any(name == item or name.startswith(item + '.') "
        "for name in sys.modules for item in forbidden)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
