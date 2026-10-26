"""Own the process startup/shutdown order, Temporal workers and channel ingress."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from account.identity_binding_service import IdentityBindingService
from application.conversation import ConversationService
from application.identity_commands import IdentityCommandService
from application.ingress import MessageIngressService
from bootstrap.durable_runtime import compose_durable_runtime
from bootstrap.worker_dependencies import init_dependencies, load_worker_qq_binding_code_pepper
from channels.napcat import NapCatChannel
from channels.official_qq import OfficialQQChannel
from common.types import ChannelType, UnifiedMessage
from memory.activities import ScheduledMemoryActivities
from memory.workflows import MetricsReportWorkflow, ReflectWorkflow
from orchestration.config import AppConfig
from orchestration.memory_schedules import setup_metrics_schedule, setup_reflect_schedule
from orchestration.worker_tasks import (
    BackgroundTasks,
    run_file_cleanup_loop,
    run_sandbox_cleanup_loop,
    start_durable_background_tasks,
    stop_worker_runtime,
    worker_readiness_loop,
)

logger = logging.getLogger("HpAgent.OrchestrationWorker")


async def start_worker(config: AppConfig) -> None:
    """完整启动流程: 组装依赖 → 连接 Temporal → 启动 Worker + 渠道监听。"""
    deps = await init_dependencies(config)

    active_channels: list = []
    background_tasks = BackgroundTasks()
    runtime_stop_attempted = False
    try:
        scheduled_memory = ScheduledMemoryActivities(
            memory_reflection=deps.shared.memory_reflection,
            metrics=deps.shared.metrics,
        )

        client = await Client.connect(config.temporal.host)
        logger.info("Temporal connected")

        worker = Worker(
            client,
            task_queue=config.temporal.task_queue,
            workflows=[ReflectWorkflow, MetricsReportWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            activities=[
                scheduled_memory.reflect,
                scheduled_memory.reflect_batch,
                scheduled_memory.metrics_report,
            ],
        )

        web_workers = None
        web_dispatcher = None
        web_reconciler = None
        web_memory_retention = None
        composition = compose_durable_runtime(client, config, deps)
        web_workers = composition.workers
        web_dispatcher = composition.dispatcher
        web_reconciler = composition.reconciler
        web_memory_retention = composition.memory_retention
        # ── 渠道注册（按 config.yaml 的 channels.enabled 列表动态加载）──
        _channel_factories = {
            ChannelType.NAPCAT: NapCatChannel,
            ChannelType.OFFICIAL_QQ: OfficialQQChannel,
        }

        active_channels: list = []
        for ch_name in config.channels.enabled:
            try:
                ch_type = ChannelType(ch_name)
            except ValueError:
                logger.warning("Unknown channel in config: %s, skipped", ch_name)
                continue

            factory = _channel_factories.get(ch_type)
            if factory is None:
                logger.warning("No implementation for channel: %s, skipped", ch_name)
                continue

            channel = factory()
            if hasattr(channel, "bot_name"):
                channel.bot_name = getattr(config.prompts, "bot_name", "bot")
            deps.qq.channel_router.register(ch_type, channel)
            active_channels.append(channel)
            logger.info("Channel registered: %s", ch_name)

        from conversation_domain.commands import CommandService
        from conversation_domain.surface_commands import SurfaceConversationCommands

        conversation_service = ConversationService(
            SurfaceConversationCommands(
                CommandService(
                    os.environ["WORKER_DATABASE_URL"],
                    budget_mode=os.getenv("RUN_BUDGET_MODE", "observe"),
                    budget_policy_version=os.getenv("RUN_BUDGET_POLICY_VERSION", "file-p0-v1"),
                )
            )
        )
        ingress_service = MessageIngressService(
            group_context=deps.infrastructure.group_context,
            conversation_service=conversation_service,
            reply_service=deps.qq.reply_service,
            identity_command_service=IdentityCommandService(
                IdentityBindingService(
                    os.environ["WORKER_DATABASE_URL"],
                    load_worker_qq_binding_code_pepper(),
                    int(os.getenv("QQ_BINDING_CHALLENGE_SECONDS", "300")),
                )
            ),
        )

        async def handle_message(message: UnifiedMessage) -> None:
            await ingress_service.handle(message)

        # ── 并发运行 Worker + 渠道监听 ──
        background_tasks.create(
            run_sandbox_cleanup_loop(deps.infrastructure.sandbox_manager, interval=300)
        )
        if deps.infrastructure.run_file_workspace is not None:
            from web_domain.file_cleanup import FileCleanupService

            cleanup_interval = int(os.getenv("FILE_CLEANUP_INTERVAL_SECONDS", "300"))
            if cleanup_interval <= 0:
                raise RuntimeError("FILE_CLEANUP_INTERVAL_SECONDS must be positive")
            background_tasks.create(
                run_file_cleanup_loop(
                    FileCleanupService(
                        deps.infrastructure.run_file_workspace.database,
                        deps.infrastructure.run_file_workspace.store,
                    ),
                    interval=cleanup_interval,
                )
            )
        async with AsyncExitStack() as worker_stack:
            await worker_stack.enter_async_context(worker)
            if (
                web_workers is not None
                and web_dispatcher is not None
                and web_reconciler is not None
            ):
                await worker_stack.enter_async_context(web_workers.lifecycle)
                logger.info("Web lifecycle worker registered: hpagent-web-lifecycle")
                await worker_stack.enter_async_context(web_workers.agent)
                logger.info("Web agent worker registered: hpagent-web-agent")
                start_durable_background_tasks(
                    tasks=background_tasks,
                    web_dispatcher=web_dispatcher,
                    web_reconciler=web_reconciler,
                    web_memory_retention=web_memory_retention,
                    lease_timeout_seconds=config.temporal.web_outbox_lease_timeout_seconds,
                    recovery_interval_seconds=config.temporal.web_outbox_recovery_interval_seconds,
                )
                logger.info("Web outbox dispatcher started")
                logger.info("Web reconciler started")
                readiness_tasks = tuple(background_tasks._tasks)
                readiness_path = Path(
                    os.getenv("HPAGENT_WORKER_READY_FILE", "/tmp/hpagent-worker-ready.json")
                )
                background_tasks.create(worker_readiness_loop(readiness_path, readiness_tasks))
            for ch in active_channels:
                await ch.start_monitor(handle_message)

            from application.qq_delivery import QQDeliveryAdapter, QQDeliveryService

            background_tasks.create(
                QQDeliveryService(
                    os.environ["WORKER_DATABASE_URL"],
                    QQDeliveryAdapter(deps.qq.channel_router),
                ).run()
            )
            channel_names = [ch.channel_type.value for ch in active_channels]
            logger.info(
                "Orchestration Worker started on task_queue='%s' (channels: %s)",
                config.temporal.task_queue,
                ", ".join(channel_names) if channel_names else "none",
            )
            logger.info("Worker startup complete")

            # 设置定期记忆反思 Schedule
            await setup_reflect_schedule(client, deps.shared.account_service, config)

            # 设置定期指标报告 Schedule（每 30 分钟）
            await setup_metrics_schedule(client, config)

            logger.info(
                "Periodic schedules configured: reflect=disabled metrics=every-30m",
            )

            try:
                await asyncio.Future()
            finally:
                # Stop ingress and dispatcher producers before Temporal Worker
                # contexts drain, then release shared infrastructure outside.
                runtime_stop_attempted = True
                await stop_worker_runtime(
                    active_channels=active_channels,
                    background_tasks=background_tasks,
                )
    finally:
        Path(os.getenv("HPAGENT_WORKER_READY_FILE", "/tmp/hpagent-worker-ready.json")).unlink(
            missing_ok=True
        )
        if not runtime_stop_attempted:
            await stop_worker_runtime(
                active_channels=active_channels,
                background_tasks=background_tasks,
            )
        await deps.close()
        logger.info("Worker shutdown complete")
