"""Scheduled memory reflection and metrics workflows."""
from datetime import timedelta
from typing import Any, Dict, List

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class ReflectWorkflow:
    """定期记忆反思 Workflow —— 由 Temporal Schedule 定期触发。

    调用 reflect_batch_activity 批量触发所有活跃账号的 Hindsight 深度推理。
    """

    @workflow.run
    async def run(self, account_ids: List[str]) -> Dict[str, Any]:
        if not account_ids:
            return {"results": {}, "total": 0}
        result = await workflow.execute_activity(
            "reflect_batch_activity",
            args=[account_ids],
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                maximum_attempts=2,
            ),
        )
        return result


@workflow.defn
class MetricsReportWorkflow:
    """定期指标报告 Workflow —— 由 Temporal Schedule 定期触发。

    调用 metrics_report_activity 采集 Hindsight 可观测性指标并以结构化 JSON 日志输出。
    """

    @workflow.run
    async def run(self) -> Dict[str, Any]:
        result = await workflow.execute_activity(
            "metrics_report_activity",
            args=[],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                maximum_attempts=2,
            ),
        )
        return result
