import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from orchestration.config import AppConfig
from orchestration.worker import start_worker


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connect", "compose"])
async def test_startup_failure_releases_shared_resources(monkeypatch, failure):
    from orchestration import worker

    scheduler = SimpleNamespace(register_handler=Mock())
    deps = SimpleNamespace(
        memory_reflection=object(),
        metrics=object(), scheduler=scheduler, mcp_manager=SimpleNamespace(disconnect=AsyncMock()),
        workspace_isolation=SimpleNamespace(close=Mock()),
    )
    monkeypatch.setattr(worker, "init_dependencies", AsyncMock(return_value=deps))
    monkeypatch.setattr(worker, "inject_scheduled_services", Mock())
    monkeypatch.setattr(worker, "Worker", Mock())
    connection = AsyncMock(side_effect=RuntimeError("connect") if failure == "connect" else None)
    monkeypatch.setattr(worker.Client, "connect", connection)
    monkeypatch.setattr(worker, "compose_web_workers", Mock(side_effect=RuntimeError("compose")))
    config = AppConfig()
    config.scheduler.enabled = False
    config.temporal.web_real_agent_enabled = True
    with pytest.raises(RuntimeError, match=failure):
        await start_worker(config)
    deps.mcp_manager.disconnect.assert_awaited_once()
    deps.workspace_isolation.close.assert_called_once()


@pytest.mark.asyncio
async def test_cancelled_workspace_waiter_does_not_leak_or_release_owners_lock():
    from workspace.isolation import AccountLockRegistry

    registry = AccountLockRegistry()
    entered = asyncio.Event()
    async def contender():
        async with registry.hold("account"):
            entered.set()
    async with registry.hold("account"):
        task = asyncio.create_task(contender())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not entered.is_set()
    async with registry.hold("account"):
        pass
