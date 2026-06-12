"""
OrchestrationWorkflow —— Temporal Workflow 纯编排层。

Workflow 不再持有任何业务数据（self._events 已移除）。
所有对话数据存储在 Redis（SessionStore），由 HarnessRunner 读写。

Workflow 只负责:
  1. 循环控制：收到消息 → process_turn_activity → 等待下一信号
  2. 信号接收：new_message（入队）/ cancel_session（终止）
  3. 状态查询：get_status()
  4. 结束归档：archive_session_activity()
"""
import asyncio
from datetime import timedelta
from typing import List, Dict, Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.worker._workflow_instance import _WorkflowBeingEvictedError


@workflow.defn
class OrchestrationWorkflow:
    """持久化编排 Workflow —— 纯编排，不持业务数据。

    会话事件全部存储在 Redis（SessionStore），
    Temporal 仅管理循环控制流和信号路由。
    """

    def __init__(self):
        self._session_id = ""
        self._account_id = ""
        self._total_turns = 0
        self._completed = False
        self._pending_messages: List[Dict[str, Any]] = []
        self._idle_timeout_minutes = 5
        self._final_content = ""

    @workflow.run
    async def run(self, user_message: Dict[str, Any]) -> Dict[str, Any]:
        """Workflow 主入口。

        流程:
          1. 记录 account_id / session_id
          2. 处理首条消息（process_turn_activity）
          3. 进入 wait_condition 循环等待后续信号
          4. 收到 cancel_session → 退出
          5. 归档会话

        Args:
            user_message: worker 构造的消息 dict。
        """
        self._account_id = user_message["account_id"]
        self._session_id = user_message["session_id"]
        if not self._account_id or not self._session_id:
            raise ValueError("account_id 和 session_id 不能为空")

        timeout = user_message.get("idle_timeout_minutes")
        if isinstance(timeout, (int, float)) and timeout > 0:
            self._idle_timeout_minutes = timeout
        elif timeout == 0:
            self._idle_timeout_minutes = 0  # 0 = 永不超时

        self._final_content = await self._process_turn(user_message)

        # 主循环 —— 等待 signal 入队的新消息
        while True:
            if self._idle_timeout_minutes == 0:
                await workflow.wait_condition(
                    lambda: bool(self._pending_messages) or self._completed
                )
            else:
                try:
                    await asyncio.wait_for(
                        workflow.wait_condition(
                            lambda: bool(self._pending_messages) or self._completed
                        ),
                        timeout=timedelta(minutes=self._idle_timeout_minutes).total_seconds(),
                    )
                except asyncio.TimeoutError:
                    workflow.logger.info("Session %s idle for %d minute(s), auto-closing",
                                        self._session_id, self._idle_timeout_minutes)
                    break

            if self._completed:
                break

            if self._pending_messages:
                next_msg = self._pending_messages.pop(0)
                self._final_content = await self._process_turn(next_msg)

        # ── 退出前：两阶段清空迟到信号 ──
        # 阶段 1: 主循环退出后、归档前（覆盖「退出到归档之间」到达的信号）
        await self._drain_pending()

        # 阶段 2: 归档。archive_session_activity 内部会调 fast 模型生成摘要，
        # 耗时可能很长（模型超时重试等），归档完成后再检查一次。
        from orchestration.worker import archive_session_activity
        await workflow.execute_activity(
            "archive_session_activity",
            args=[self._session_id],
            start_to_close_timeout=timedelta(seconds=60),
        )

        # 阶段 3: 归档后二次 drain（覆盖「归档过程中」到达的信号）
        re_archived = await self._drain_pending()
        if re_archived:
            await workflow.execute_activity(
                "archive_session_activity",
                args=[self._session_id],
                start_to_close_timeout=timedelta(seconds=60),
            )

        return {
            "status": "completed",
            "content": self._final_content,
            "turns": self._total_turns,
            "account_id": self._account_id,
            "session_id": self._session_id,
        }

    async def _drain_pending(self) -> bool:
        """等待并消费 _pending_messages 中的迟到 signal。

        Returns:
            True 如果有消息被消费（调用方可能需要重新归档）。
        """
        try:
            await asyncio.wait_for(
                workflow.wait_condition(
                    lambda: bool(self._pending_messages),
                ),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            return False  # 无迟到信号

        drained = False
        while self._pending_messages:
            next_msg = self._pending_messages.pop(0)
            workflow.logger.info(
                "Session %s: draining late signal",
                self._session_id,
            )
            self._final_content = await self._process_turn(next_msg)
            drained = True
        return drained

    async def _process_turn(self, user_message: Dict[str, Any]) -> str:
        """处理一条消息 —— 委托给 HarnessRunner.process_turn_activity。"""
        self._total_turns += 1

        timeout_seconds = user_message.get("activity_timeout", 300)

        try:
            from orchestration.worker import process_turn_activity
            result = await workflow.execute_activity(
                "process_turn_activity",
                args=[user_message],
                start_to_close_timeout=timedelta(seconds=timeout_seconds),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=60),
                    maximum_attempts=1,
                ),
            )
        except _WorkflowBeingEvictedError:
            raise
        except Exception:
            workflow.logger.exception("process_turn_activity failed for session %s", self._session_id)
            return ""

        return result.get("content", "")

    # ══════════════════════════════════════════════════════════════════════
    # Signals —— 外部向运行中的 Workflow 发送指令
    # ══════════════════════════════════════════════════════════════════════

    @workflow.signal
    async def new_message(self, user_message: Dict[str, Any]) -> None:
        """入队一条新的用户消息。

        Worker 检测到同一 account_id 的 Workflow 已运行时调用。
        消息入队 pending_messages，wait_condition 被唤醒后取出处理。
        事件写入由 HarnessRunner 在 process_turn 中完成。
        """
        self._pending_messages.append(user_message)

    @workflow.signal
    async def cancel_session(self) -> None:
        """终止信号 —— 主循环退出，触发归档。"""
        self._completed = True

    # ══════════════════════════════════════════════════════════════════════
    # Queries —— 外部查询运行中 Workflow 的状态
    # ══════════════════════════════════════════════════════════════════════

    @workflow.query
    def get_status(self) -> Dict[str, Any]:
        """返回 Workflow 执行状态快照。"""
        return {
            "turns": self._total_turns,
            "completed": self._completed,
            "account_id": self._account_id,
            "session_id": self._session_id,
        }


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
