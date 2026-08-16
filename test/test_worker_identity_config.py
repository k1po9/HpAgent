from __future__ import annotations

import pytest

from orchestration.worker import load_worker_qq_binding_code_pepper


def test_worker_production_qq_pepper_fails_closed(monkeypatch):
    monkeypatch.setenv("HPAGENT_ENV", "production")
    monkeypatch.delenv("QQ_BINDING_CODE_PEPPER", raising=False)
    with pytest.raises(RuntimeError, match="explicitly set"):
        load_worker_qq_binding_code_pepper()

    monkeypatch.setenv("QQ_BINDING_CODE_PEPPER", "short")
    with pytest.raises(RuntimeError, match="32 bytes"):
        load_worker_qq_binding_code_pepper()

    monkeypatch.setenv("QQ_BINDING_CODE_PEPPER", "q" * 32)
    assert load_worker_qq_binding_code_pepper() == b"q" * 32


def test_worker_development_qq_pepper_has_safe_default(monkeypatch):
    monkeypatch.setenv("HPAGENT_ENV", "development")
    monkeypatch.delenv("QQ_BINDING_CODE_PEPPER", raising=False)
    assert len(load_worker_qq_binding_code_pepper()) >= 32
