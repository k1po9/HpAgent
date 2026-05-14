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

        final_content = await self._process_turn(user_message)

        # 主循环 —— 等待 signal 入队的新消息
        while True:
            await workflow.wait_condition(
                lambda: bool(self._pending_messages) or self._completed
            )
            if self._completed:
                break
            if self._pending_messages:
                next_msg = self._pending_messages.pop(0)
                final_content = await self._process_turn(next_msg)

        # 退出前归档会话
        await workflow.execute_activity(
            "archive_session_activity",
            args=[self._session_id],
            start_to_close_timeout=timedelta(seconds=10),
        )

        return {
            "status": "completed",
            "content": final_content,
            "turns": self._total_turns,
            "account_id": self._account_id,
            "session_id": self._session_id,
        }

    async def _process_turn(self, user_message: Dict[str, Any]) -> str:
        """处理一条消息 —— 委托给 HarnessRunner.process_turn_activity。"""
        self._total_turns += 1

        try:
            from orchestration.worker import process_turn_activity
            result = await workflow.execute_activity(
                "process_turn_activity",
                args=[user_message],
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=60),
                    maximum_attempts=2,
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
