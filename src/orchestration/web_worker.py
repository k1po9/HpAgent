"""独立 Web real-Agent Worker 入口（真正独立的进程边界）。

本进程只运行 Web 相关的 Temporal Worker（lifecycle + agent）、start/cancel
Outbox Dispatcher、Reconciler 与过期 lease 自动恢复循环。它绝不注册 QQ 渠道、
绝不轮询任务调度器、绝不创建周期 Schedule —— 这些都属于单一 Worker 进程
（``orchestration.worker.start_worker``）。

拓扑门禁（fail-closed）:
  - ``single_process_account_lock``（当前默认，也是唯一已实现拓扑）:
    QQ 与 Web 必须共享同一进程、同一 ``AccountLockRegistry``（AE-021）。
    第二个进程会与主 Worker 竞争 workspace 进程锁、割裂共享锁注册表，
    因此本入口在该模式下拒绝启动。
  - ``session_worktree``（目前仅枚举值，尚未实现）: 允许独立 Web Worker
    进程的结构化拓扑。该模式下 ``WorkspaceIsolationRuntime.start()`` 为空操作，
    本入口组合 Web-only 组件后前台阻塞运行。

主要部署路径是把 Web 合并进主 ``hpagent`` Worker（设置
``WEB_REAL_AGENT_ENABLED=true`` 等环境变量），见 docker-compose.yaml。
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path

# ── 项目根目录检测（日志在所有模块导入之前配置）──
_base = Path(__file__).resolve().parent
_config_dir = _base / "config"
if not _config_dir.exists():
    _config_dir = _base.parent / "config"
_project_root = _config_dir.parent

# ── 加载 .env 环境变量（必须在所有模块导入之前）──
try:
    from dotenv import load_dotenv

    _env_file = _project_root / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass

# ── 日志配置（数据路径锚定到项目根目录）──
from common.logging import setup_logging

_log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
_log_dir = Path(os.getenv("LOG_DIR", str(_project_root / ".data/logs")))
setup_logging(level=_log_level, log_dir=_log_dir)

logger = logging.getLogger("HpAgent.WebWorker")

from temporalio.client import Client

from orchestration.config import AppConfig
from orchestration.memory_retention_worker import run_memory_retention_loop
from orchestration.web_dispatcher import run_web_outbox_recovery_loop
from orchestration.web_workers import validate_standalone_web_worker_topology
from orchestration.worker import (
    _run_web_dispatcher_loop,
    _run_web_reconciler_loop,
    compose_web_workers,
    init_dependencies,
)


async def main_async() -> None:
    """独立 Web Worker 主流程：加载配置 → 门禁 → 组合 → 前台阻塞。"""
    config_path = _config_dir / "config.yaml"
    try:
        config = AppConfig.from_yaml(str(config_path))
    except FileNotFoundError:
        logger.error("Config file not found: %s", config_path)
        return

    logger.info("=== HpAgent Standalone Web Worker ===")
    logger.info("Temporal: %s", config.temporal.host)

    # ── 拓扑门禁：single_process_account_lock 下禁止独立 Web 进程 ──
    validate_standalone_web_worker_topology(
        config.workspace.workspace_isolation_mode,
        config.temporal.web_real_agent_enabled,
    )

    deps = await init_dependencies(config)
    client = await Client.connect(config.temporal.host)
    composition = compose_web_workers(client, config, deps)

    dispatcher_task: asyncio.Task | None = None
    reconciler_task: asyncio.Task | None = None
    recovery_task: asyncio.Task | None = None
    memory_retention_task: asyncio.Task | None = None
    memory_retention_recovery_task: asyncio.Task | None = None
    try:
        async with AsyncExitStack() as worker_stack:
            await worker_stack.enter_async_context(composition.workers.lifecycle)
            await worker_stack.enter_async_context(composition.workers.agent)
            dispatcher_task = asyncio.create_task(
                _run_web_dispatcher_loop(composition.dispatcher)
            )
            reconciler_task = asyncio.create_task(
                _run_web_reconciler_loop(composition.reconciler)
            )
            recovery_task = asyncio.create_task(
                run_web_outbox_recovery_loop(
                    composition.dispatcher.outbox,
                    config.temporal.web_outbox_lease_timeout_seconds,
                    config.temporal.web_outbox_recovery_interval_seconds,
                )
            )
            if composition.memory_retention is not None:
                memory_retention_task = asyncio.create_task(
                    run_memory_retention_loop(
                        composition.dispatcher.outbox,
                        composition.memory_retention,
                        worker_id=f"hpagent-memory-{os.getpid()}",
                    )
                )
                memory_retention_recovery_task = asyncio.create_task(
                    run_web_outbox_recovery_loop(
                        composition.dispatcher.outbox,
                        config.temporal.web_outbox_lease_timeout_seconds,
                        config.temporal.web_outbox_recovery_interval_seconds,
                        event_types={"retain_memory"},
                    )
                )
            logger.info(
                "Standalone Web Worker started"
                " (lifecycle + agent + dispatcher + reconciler + recovery"
                + (" + memory-retention" if composition.memory_retention is not None else "")
                + ")"
            )
            await asyncio.Future()
    finally:
        _background = (
            dispatcher_task,
            reconciler_task,
            recovery_task,
            memory_retention_task,
            memory_retention_recovery_task,
        )
        for task in _background:
            if task is not None:
                task.cancel()
        for task in _background:
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if deps.mcp_manager is not None:
            try:
                await deps.mcp_manager.disconnect()
                logger.info("MCP connections closed")
            except Exception as e:
                logger.warning("MCP disconnect failed: %s", e)
        if deps.workspace_isolation is not None:
            deps.workspace_isolation.close()
        logger.info("Standalone Web Worker shutdown complete")


def main() -> None:
    """同步包装入口。"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
