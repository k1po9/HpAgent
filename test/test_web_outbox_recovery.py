"""Contract tests for Web Outbox expired-lease auto-recovery (Phase D closing).

The recovery sweep is a production background loop that must:
  - run on a config-driven cadence, separate from the ~250ms Dispatcher poll;
  - pass the config-driven lease timeout to ``OutboxService.recover_expired``
    (never ``recover_expired(0)``);
  - log an iteration error and keep looping (a transient DB failure must not
    kill the recovery task);
  - cancel cleanly on worker shutdown so the caller can await the task.

The config validation (both values positive, interval < timeout) belongs to the
Web worker startup gate and is tested here too.

These tests connect to no PostgreSQL and no Temporal Server.
"""
from __future__ import annotations

import asyncio

import pytest

from orchestration.config import AppConfig, TemporalConfig
from orchestration.web_dispatcher import run_web_outbox_recovery_loop
from orchestration.web_workers import (
    WEB_REAL_AGENT_GATE_VERSION,
    validate_web_worker_startup,
)


class _RecordingOutbox:
    def __init__(self, failures: int = 0):
        self.calls: list[int] = []
        self._failures = failures

    def recover_expired(self, older_than_seconds: int) -> int:
        self.calls.append(older_than_seconds)
        if len(self.calls) <= self._failures:
            raise RuntimeError("transient database failure")
        return 0


@pytest.mark.asyncio
async def test_recovery_loop_runs_on_config_cadence_with_config_timeout_and_cancels():
    outbox = _RecordingOutbox()
    loop = asyncio.create_task(run_web_outbox_recovery_loop(outbox, 60, 0.01))
    await asyncio.sleep(0.06)
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop

    # The sweep repeats on its own config-driven cadence, not once and not in
    # the fast poll, and every call uses the configured lease timeout (never 0).
    assert len(outbox.calls) >= 2, "recovery must run repeatedly on the interval"
    assert outbox.calls == [60] * len(outbox.calls)


@pytest.mark.asyncio
async def test_recovery_loop_logs_an_error_and_keeps_looping():
    outbox = _RecordingOutbox(failures=1)
    loop = asyncio.create_task(run_web_outbox_recovery_loop(outbox, 30, 0.01))
    await asyncio.sleep(0.06)
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop

    # The transient failure was logged; the loop continued with later sweeps.
    assert len(outbox.calls) >= 2, "recovery must keep looping after an error"
    assert outbox.calls == [30] * len(outbox.calls)


def test_outbox_recovery_config_defaults():
    config = TemporalConfig()
    assert config.web_outbox_lease_timeout_seconds == 60
    assert config.web_outbox_recovery_interval_seconds == 15


def test_outbox_recovery_env_overrides():
    config = AppConfig()
    config._apply_env_overrides(
        {
            "WEB_OUTBOX_LEASE_TIMEOUT_SECONDS": "90",
            "WEB_OUTBOX_RECOVERY_INTERVAL_SECONDS": "20",
        }
    )
    assert config.temporal.web_outbox_lease_timeout_seconds == 90
    assert config.temporal.web_outbox_recovery_interval_seconds == 20


def test_web_worker_startup_rejects_invalid_outbox_recovery_config():
    config = TemporalConfig()
    config.web_real_agent_gate_version = WEB_REAL_AGENT_GATE_VERSION

    config.web_outbox_lease_timeout_seconds = 0
    with pytest.raises(RuntimeError, match="lease_timeout_seconds must be positive"):
        validate_web_worker_startup(config, "postgresql://worker")
    config.web_outbox_lease_timeout_seconds = 60

    config.web_outbox_recovery_interval_seconds = 0
    with pytest.raises(RuntimeError, match="recovery_interval_seconds must be positive"):
        validate_web_worker_startup(config, "postgresql://worker")
    config.web_outbox_recovery_interval_seconds = 15

    # interval must be strictly smaller than the timeout.
    config.web_outbox_lease_timeout_seconds = 15
    config.web_outbox_recovery_interval_seconds = 15
    with pytest.raises(RuntimeError, match="must be smaller than"):
        validate_web_worker_startup(config, "postgresql://worker")

    config.web_outbox_lease_timeout_seconds = 60
    validate_web_worker_startup(config, "postgresql://worker")
