import asyncio
from pathlib import Path
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
        shared=SimpleNamespace(memory_reflection=object(), metrics=object()),
        scheduler=scheduler,
        close=AsyncMock(),
    )
    monkeypatch.setattr(worker, "init_dependencies", AsyncMock(return_value=deps))
    monkeypatch.setattr(worker, "Worker", Mock())
    connection = AsyncMock(side_effect=RuntimeError("connect") if failure == "connect" else None)
    monkeypatch.setattr(worker.Client, "connect", connection)
    monkeypatch.setattr(worker, "compose_durable_runtime", Mock(side_effect=RuntimeError("compose")))
    config = AppConfig()
    config.scheduler.enabled = False
    with pytest.raises(RuntimeError, match=failure):
        await start_worker(config)
    deps.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_failure_unwinds_resources_registered_during_construction(monkeypatch):
    from orchestration import worker

    closed = AsyncMock()

    async def fail_after_acquisition(_config, stack):
        stack.push_async_callback(closed)
        raise RuntimeError("late init failure")

    monkeypatch.setattr(worker, "_init_dependencies", fail_after_acquisition)
    with pytest.raises(RuntimeError, match="late init failure"):
        await worker.init_dependencies(AppConfig())
    closed.assert_awaited_once()


def test_sandbox_manager_close_destroys_every_owned_sandbox():
    from sandbox.sandbox_manager import SandboxManager

    first = SimpleNamespace(destroy=Mock())
    second = SimpleNamespace(destroy=Mock())
    manager = SandboxManager(native_tools_enabled=False)
    manager._sandboxes = {"one": first, "two": second}
    manager._session_to_sandbox = {"session-one": "one", "session-two": "two"}
    manager._run_file_scopes = {"run": object()}
    manager._session_active_file_run = {"session-one": "run"}

    manager.close()

    first.destroy.assert_called_once_with()
    second.destroy.assert_called_once_with()
    assert manager._sandboxes == {}
    assert manager._session_to_sandbox == {}
    assert manager._run_file_scopes == {}
    assert manager._session_active_file_run == {}


@pytest.mark.asyncio
async def test_web_lifespan_startup_failure_closes_acquired_pool(monkeypatch):
    from web_api import app as app_module
    from web_api.config import WebApiSettings

    pool = SimpleNamespace(wait=Mock(), close=Mock())
    monkeypatch.setattr(app_module, "ConnectionPool", Mock(return_value=pool))
    settings = WebApiSettings(
        database_url="postgresql://unused",
        public_origin="https://example.test",
        cursor_signing_keys={"v1": b"x" * 32},
        active_cursor_key_id="v1",
        session_token_pepper=b"y" * 32,
        csrf_signing_key=b"z" * 32,
        environment="test",
        web_file_upload_enabled=True,
        file_store_root=str(Path.cwd()),
    )
    app = app_module.create_app(settings, object())

    with pytest.raises(RuntimeError, match="outside the application"):
        async with app.router.lifespan_context(app):
            pass

    pool.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_worker_stops_background_producers_before_temporal_workers(monkeypatch):
    from application import qq_delivery
    from orchestration import worker

    events: list[str] = []
    ready = asyncio.Event()

    class AsyncContext:
        def __init__(self, name):
            self.name = name

        async def __aenter__(self):
            events.append(f"{self.name}_enter")
            return self

        async def __aexit__(self, *_args):
            events.append(f"{self.name}_exit")

    async def owned_loop():
        try:
            await asyncio.Future()
        finally:
            events.append("background_stopped")

    class Delivery:
        def __init__(self, *_args):
            pass

        async def run(self):
            await asyncio.Future()

    class Router:
        def register(self, *_args):
            pass

        async def send(self, *_args):
            return True

    async def close_dependencies():
        events.append("dependencies_closed")

    deps = SimpleNamespace(
        shared=SimpleNamespace(
            memory_reflection=object(), metrics=object(), account_service=object()
        ),
        infrastructure=SimpleNamespace(
            sandbox_manager=SimpleNamespace(cleanup_idle_sandboxes=lambda: 0),
            run_file_workspace=None,
            group_context=None,
        ),
        qq=SimpleNamespace(channel_router=Router(), reply_service=object()),
        scheduler=SimpleNamespace(register_handler=Mock()),
        close=close_dependencies,
    )
    composition = SimpleNamespace(
        workers=SimpleNamespace(
            lifecycle=AsyncContext("lifecycle"), agent=AsyncContext("agent")
        ),
        dispatcher=object(),
        reconciler=object(),
        memory_retention=None,
        artifact_dispatcher=None,
    )

    monkeypatch.setattr(worker, "init_dependencies", AsyncMock(return_value=deps))
    monkeypatch.setattr(worker.Client, "connect", AsyncMock(return_value=object()))
    monkeypatch.setattr(worker, "Worker", Mock(return_value=AsyncContext("scheduled")))
    monkeypatch.setattr(worker, "compose_durable_runtime", Mock(return_value=composition))
    monkeypatch.setattr(
        worker,
        "_build_web_background_tasks",
        lambda *, tasks, **_kwargs: tasks.create(owned_loop()),
    )
    monkeypatch.setattr(qq_delivery, "QQDeliveryService", Delivery)
    monkeypatch.setattr(worker, "_setup_reflect_schedule", AsyncMock())

    async def metrics_ready(*_args):
        ready.set()

    monkeypatch.setattr(worker, "_setup_metrics_schedule", metrics_ready)
    monkeypatch.setenv("WORKER_DATABASE_URL", "postgresql://unused")
    config = AppConfig()
    config.scheduler.enabled = False
    config.channels.enabled = []

    task = asyncio.create_task(start_worker(config))
    await asyncio.wait_for(ready.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events.index("background_stopped") < events.index("agent_exit")
    assert events.index("agent_exit") < events.index("lifecycle_exit")
    assert events.index("lifecycle_exit") < events.index("scheduled_exit")
    assert events.index("scheduled_exit") < events.index("dependencies_closed")


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
