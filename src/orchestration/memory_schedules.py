"""Temporal schedules for memory reflection and metrics; Work schedules remain in PostgreSQL."""

from __future__ import annotations

import logging

from memory.workflows import MetricsReportWorkflow

logger = logging.getLogger("HpAgent.OrchestrationWorker")


async def setup_reflect_schedule(client, account_service, config) -> None:
    """Remove the obsolete whole-bank Reflect schedule; native Consolidation owns summaries."""
    from temporalio.service import RPCError, RPCStatusCode

    try:
        await client.get_schedule_handle("hpagent-reflect-schedule").delete()
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise


async def setup_metrics_schedule(client, config) -> None:
    """创建 Temporal Schedule，每 30 分钟触发一次 MetricsReportWorkflow。

    MetricsReportWorkflow 调用 metrics_report_activity 采集 Hindsight
    可观测性指标并以结构化 JSON 日志输出，供外部监控系统（Prometheus/
    Grafana/ELK）采集。
    """
    from datetime import timedelta

    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleOverlapPolicy,
        SchedulePolicy,
        ScheduleSpec,
        ScheduleState,
    )

    schedule_id = "hpagent-metrics-schedule"
    try:
        # 先清理旧 schedule 再创建（upsert 语义）
        try:
            handle = client.get_schedule_handle(schedule_id)
            await handle.delete()
        except Exception:
            pass

        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    MetricsReportWorkflow.run,
                    id="hpagent-metrics-report",
                    task_queue=config.temporal.task_queue,
                ),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(minutes=30))]),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
                state=ScheduleState(note="Hindsight 可观测性指标报告 —— 每 30 分钟采集一次"),
            ),
        )
        logger.info("Metrics report schedule created: id=%s every=30m", schedule_id)
    except Exception as e:
        logger.warning("Failed to create metrics schedule: %s", e)
