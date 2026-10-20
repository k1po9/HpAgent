"""Process-owned background producers, readiness and orderly cancellation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("HpAgent.OrchestrationWorker")


class BackgroundTasks:
    """Composition-owned set of cancellable process background tasks."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def create(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        return task

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def start_durable_background_tasks(
    *,
    tasks: BackgroundTasks,
    web_dispatcher,
    web_reconciler,
    web_memory_retention,
    lease_timeout_seconds: int,
    recovery_interval_seconds: float,
) -> None:
    """把 Web 组合产物变成可取消的后台任务，供 ``start_worker`` 前台运行。

    Dispatcher / Reconciler / 过期 lease 恢复 / Memory Retention（Phase F）各占
    一条独立任务，从不共享 lease 所有权。``web_memory_retention`` 必须来自
    ``DurableRuntimeComposition.memory_retention``（组合层）；它**不在**
    ``composition.workers`` 上 —— 那里只有 lifecycle/agent（C-07）。
    """
    # Canonical Web/QQ workers are composed here without module-level Temporal side effects.
    from orchestration.web_dispatcher import run_web_outbox_recovery_loop
    from orchestration.work_schedule import WorkScheduleService, run_work_schedule_loop

    tasks.create(
        run_work_schedule_loop(
            WorkScheduleService(
                web_dispatcher.outbox.database_url,
                budget_mode=os.getenv("RUN_BUDGET_MODE", "enforce"),
            )
        )
    )
    tasks.create(run_dispatcher_loop(web_dispatcher))
    tasks.create(run_reconciler_loop(web_reconciler))
    # Expired-lease auto-recovery runs on its own cadence, never in the fast
    # Dispatcher poll; validate_web_worker_startup already guaranteed both
    # values are positive and interval < timeout.
    tasks.create(
        run_web_outbox_recovery_loop(
            web_dispatcher.outbox,
            lease_timeout_seconds,
            recovery_interval_seconds,
        )
    )
    if web_memory_retention is not None:
        # Phase F: retain_memory consumer + its own expired-lease sweep.  Both
        # run independently of the start/cancel Dispatcher and never share its
        # lease ownership.
        from orchestration.memory_retention_worker import (
            run_memory_retention_loop,
        )

        tasks.create(
            run_memory_retention_loop(
                web_dispatcher.outbox,
                web_memory_retention,
                worker_id=f"hpagent-memory-{os.getpid()}",
            )
        )
        tasks.create(
            run_web_outbox_recovery_loop(
                web_dispatcher.outbox,
                lease_timeout_seconds,
                recovery_interval_seconds,
                event_types={"retain_memory"},
            )
        )


async def worker_readiness_loop(path: Path, critical_tasks: tuple[asyncio.Task, ...]) -> None:
    """Refresh readiness only while the dispatcher and reconciler loops live."""
    while True:
        if any(task.done() for task in critical_tasks):
            path.unlink(missing_ok=True)
            logger.error("Worker readiness lost: background task exited")
            return
        payload = {
            "pid": os.getpid(),
            "updated_at": time.time(),
            "schema_verified": True,
            "temporal_connected": True,
            "lifecycle_worker_started": True,
            "agent_worker_started": True,
            "dispatcher_started": True,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload))
        temporary.replace(path)
        await asyncio.sleep(5)


async def stop_worker_runtime(
    *,
    active_channels,
    background_tasks: BackgroundTasks,
) -> None:
    """Stop ingress and background producers before Temporal Workers drain."""
    logger.info("Worker stopping channels and background tasks...")

    for ch in active_channels:
        ch_name = ch.channel_type.value
        try:
            await ch.stop_monitor()
            logger.info("%s monitor stopped", ch_name)
        except Exception as e:
            logger.warning("%s stop_monitor failed: %s", ch_name, e)

    await background_tasks.close()


async def run_sandbox_cleanup_loop(sandbox_manager, interval: int = 300) -> None:
    """后台任务：每 interval 秒清理一次闲置沙箱。"""
    while True:
        try:
            await asyncio.sleep(interval)
            cleaned = sandbox_manager.cleanup_idle_sandboxes()
            if cleaned > 0:
                logger.info("Sandbox cleanup: %d idle sandboxes destroyed", cleaned)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Sandbox cleanup error: %s", e)


async def run_file_cleanup_loop(service, interval: int = 300) -> None:
    """Delete expired unbound objects without blocking the event loop."""
    while True:
        try:
            result = await asyncio.to_thread(service.cleanup_once)
            if result.claimed:
                logger.info(
                    "File cleanup: claimed=%d deleted=%d failed=%d",
                    result.claimed,
                    result.deleted,
                    result.failed,
                )
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("File cleanup iteration failed")
            await asyncio.sleep(interval)


async def run_dispatcher_loop(dispatcher, idle_seconds: float = 0.25) -> None:
    """Continuously consume Web start/cancel Outbox events."""
    while True:
        try:
            handled = await dispatcher.run_once()
            if not handled:
                await asyncio.sleep(idle_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Web Outbox Dispatcher iteration failed")
            await asyncio.sleep(1)


async def run_reconciler_loop(reconciler, interval_seconds: float = 5) -> None:
    """Periodically converge active Runs and Temporal execution facts."""
    while True:
        try:
            await reconciler.run_once()
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Web Reconciler iteration failed")
            await asyncio.sleep(interval_seconds)
