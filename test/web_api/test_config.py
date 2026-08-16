from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def test_real_web_agent_feature_flag_defaults_to_off(monkeypatch):
    monkeypatch.delenv("WEB_REAL_AGENT_ENABLED", raising=False)
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://unused")
    assert WebApiSettings.from_env().real_agent_enabled is False


# ── Phase G G-02 §10.2：生产 WEB_PUBLIC_ORIGIN fail-closed ──


def _env_settings(monkeypatch, **env):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://unused")
    # 生产校验要求 secrets ≥ 32 bytes（G §10/§11），默认给足长值。
    monkeypatch.setenv("WEB_CURSOR_SECRET", "x" * 40)
    monkeypatch.setenv("WEB_SESSION_TOKEN_PEPPER", "y" * 40)
    monkeypatch.setenv("WEB_CSRF_SIGNING_KEY", "z" * 40)
    monkeypatch.setenv("QQ_BINDING_CODE_PEPPER", "q" * 40)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return WebApiSettings.from_env()


def test_production_rejects_missing_public_origin(monkeypatch):
    with pytest.raises(ValueError, match="WEB_PUBLIC_ORIGIN"):
        _env_settings(
            monkeypatch,
            HPAGENT_ENV="production",
            WEB_PUBLIC_ORIGIN=None,
        )


def test_production_rejects_dev_default_public_origin(monkeypatch):
    with pytest.raises(ValueError, match="real public HTTPS origin"):
        _env_settings(
            monkeypatch,
            HPAGENT_ENV="production",
            WEB_PUBLIC_ORIGIN="https://localhost",
        )


def test_production_rejects_http_public_origin(monkeypatch):
    with pytest.raises(ValueError, match="https"):
        _env_settings(
            monkeypatch,
            HPAGENT_ENV="production",
            WEB_PUBLIC_ORIGIN="http://hpagent-api:8080",
        )


def test_production_accepts_real_https_origin(monkeypatch):
    settings = _env_settings(
        monkeypatch,
        HPAGENT_ENV="production",
        WEB_PUBLIC_ORIGIN="https://agent.example.com",
    )
    assert settings.public_origin == "https://agent.example.com"
    assert settings.environment == "production"


def test_development_allows_localhost_origin(monkeypatch):
    settings = _env_settings(
        monkeypatch,
        HPAGENT_ENV="development",
        WEB_PUBLIC_ORIGIN="https://localhost",
    )
    assert settings.public_origin == "https://localhost"


def test_production_requires_explicit_qq_binding_pepper(monkeypatch):
    with pytest.raises(ValueError, match="QQ_BINDING_CODE_PEPPER"):
        _env_settings(
            monkeypatch,
            HPAGENT_ENV="production",
            WEB_PUBLIC_ORIGIN="https://agent.example.com",
            QQ_BINDING_CODE_PEPPER=None,
        )


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
    env = os.environ.copy()
    source_root = Path(__file__).resolve().parents[2] / "src"
    inherited_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{inherited_pythonpath}"
        if inherited_pythonpath
        else str(source_root)
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
