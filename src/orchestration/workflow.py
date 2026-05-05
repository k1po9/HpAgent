"""
OrchestrationWorkflow —— 指挥层的 Temporal Workflow 定义。

这是整个 agentic loop 的确定性编排核心。Workflow 是长期运行的：
  1. 收到首条消息 → 启动 Workflow
  2. 进入 agentic loop（context → model → tools → response）
  3. 通过 signal (new_message) 接收后续消息，入队后继续循环
  4. 通过 wait_condition 等待新消息
  5. cancel_session signal → 终止 Workflow

跨客户端记忆共享:
  - workflow_id = f"agent-{account_id}"（基于统一账号 ID）
  - QQ 和 Web 端消息通过同一个 account_id 路由到同一个 Workflow
  - self._events[] 混合存储 QQ + Web 的事件历史，实现无缝共享

所有非确定性操作（模型调用、工具执行、上下文构建、渠道 I/O）
委托给 Temporal Activities —— Harness 层的无状态大脑操作。
"""
from datetime import timedelta
from typing import List, Dict, Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class OrchestrationWorkflow:
    """持久化编排 Workflow —— agentic loop 的执行引擎。

    Temporal 保证 Workflow 的执行是确定性的，即使 Worker 崩溃重启，
    Workflow 也会从上次的 event history 精确恢复到中断点。

    事件存储:
      self._events: 所有事件（QQ + Web 混存），按时间顺序追加。
      每条事件是 dict，包含 type / content / sender_id / channel_type / timestamp。

    状态属性:
      _max_turns:      单条消息的最大推理轮次（防止无限循环）
      _total_turns:    整个 Workflow 生命周期的总轮次计数
      _completed:      是否已收到 cancel_session 信号
      _pending_messages: signal 入队的待处理消息队列
    """

    def __init__(self):
        self._events: List[Dict[str, Any]] = []          # 事件历史（确定性状态）
        self._max_turns = 20                              # 单条消息最大推理轮次
        self._total_turns = 0                             # 总轮次计数
        self._completed = False                           # 终止标志
        self._account_id = ""                             # 统一账号 ID
        self._session_id = ""                             # 当前会话 ID
        self._pending_messages: List[Dict[str, Any]] = [] # 待处理消息队列

    @workflow.run
    async def run(self, user_message: Dict[str, Any]) -> Dict[str, Any]:
        """Workflow 主入口（由 Temporal 自动调用）。

        首次调用流程:
          1. 记录 account_id 和 session_id
          2. 追加 USER_MESSAGE 事件到历史
          3. 调用 _process_turn 执行完整 agentic loop
          4. 进入 wait_condition 循环，等待 signal 入队的新消息
          5. 收到 cancel_session 或 Workflow 被外部终止时退出

        Args:
            user_message: worker 构造的消息 dict，含 content / sender_id / channel_type
                          / account_id / session_id / metadata / timestamp。

        Returns:
            {"status": "completed", "content": str, "turns": int, "event_count": int, ...}
        """
        self._account_id = user_message.get("account_id", "")
        self._session_id = user_message.get("session_id", "")

        # 追加首条用户消息到事件历史
        self._events.append({
            "type": "USER_MESSAGE",
            "content": user_message.get("content", ""),
            "sender_id": user_message.get("sender_id", ""),
            "channel_type": user_message.get("channel_type", "console"),
            "account_id": self._account_id,
            "session_id": self._session_id,
            "timestamp": user_message.get("timestamp", 0),
        })

        # 处理首条消息
        final_content = await self._process_turn(user_message)

        # 主循环 —— 等待 signal 入队的新消息，逐一处理
        while True:
            # wait_condition: 阻塞直到队列非空或收到终止信号
            await workflow.wait_condition(
                lambda: bool(self._pending_messages) or self._completed
            )
            if self._completed:
                break
            if self._pending_messages:
                next_msg = self._pending_messages.pop(0)
                final_content = await self._process_turn(next_msg)

        return {
            "status": "completed",
            "content": final_content,
            "turns": self._total_turns,
            "event_count": len(self._events),
            "account_id": self._account_id,
            "session_id": self._session_id,
        }

    async def _process_turn(self, user_message: Dict[str, Any]) -> str:
        """执行一条消息的完整 agentic loop。

        Loop 结构（每轮 = 4 个 Activity 调用）:
          1. build_context_activity     → events[] → LLM messages[]
          2. get_available_tools_activity → 沙箱 → 工具定义列表
          3. call_model_activity         → LLM → 文本回复 + tool_calls
          4. 如果有 tool_calls → execute_tool_activity → 工具结果 → 回到步骤 1
          5. 如果没有 tool_calls → send_response_activity → 渠道输出 → 结束

        Args:
            user_message: 当前处理的消息 dict。

        Returns:
            模型最终文本回复内容。
        """
        final_content = ""
        turn_completed = False
        msg_turns = 0

        while msg_turns < self._max_turns and not turn_completed and not self._completed:
            msg_turns += 1
            self._total_turns += 1
            channel_type = user_message.get("channel_type", "console")

            # ── 步骤 1: 构建上下文（大脑组装记忆） ──
            context = await workflow.execute_activity(
                "build_context_activity",
                args=[self._events, channel_type],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # ── 步骤 2: 获取工具列表（大脑清点双手能力） ──
            tools = await workflow.execute_activity(
                "get_available_tools_activity",
                args=[],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # ── 步骤 3: 调用模型（大脑调用 LLM） ──
            # 最多重试 3 次，退避间隔 1s → 逐步递增至 60s
            model_response = await workflow.execute_activity(
                "call_model_activity",
                args=[context, tools],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=60),
                    maximum_attempts=3,
                ),
            )

            # 追加模型回复事件
            self._events.append({
                "type": "MODEL_MESSAGE",
                "content": model_response.get("content", ""),
                "tool_calls": model_response.get("tool_calls", []),
                "stop_reason": model_response.get("stop_reason", ""),
            })

            final_content = model_response.get("content", "")

            # ── 步骤 4: 执行工具（大脑指挥双手） ──
            tool_calls = model_response.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    result = await workflow.execute_activity(
                        "execute_tool_activity",
                        args=[tc.get("name", ""), tc.get("arguments", {})],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=1),
                            maximum_attempts=2,
                        ),
                    )
                    self._events.append({
                        "type": "TOOL_RESULT",
                        "tool_call_id": tc.get("id", ""),
                        "tool_name": tc.get("name", ""),
                        "result": result,
                    })
            else:
                # 无工具调用 → 模型给出了最终回复 → 本轮完成
                turn_completed = True

        # ── 步骤 5: 发送响应（大脑通过渠道说话） ──
        await workflow.execute_activity(
            "send_response_activity",
            args=[final_content, user_message],
            start_to_close_timeout=timedelta(seconds=10),
        )

        return final_content

    # ══════════════════════════════════════════════════════════════════════
    # Temporal Signals —— 外部向运行中的 Workflow 发送指令
    # ══════════════════════════════════════════════════════════════════════

    @workflow.signal
    async def new_message(self, user_message: Dict[str, Any]) -> None:
        """入队一条新的用户消息。

        由 worker 在检测到同一 account_id 的 Workflow 已在运行时调用。
        消息先追加到事件历史，再入队 pending_messages，
        wait_condition 被唤醒后由主循环取出处理。
        """
        self._events.append({
            "type": "USER_MESSAGE",
            "content": user_message.get("content", ""),
            "sender_id": user_message.get("sender_id", ""),
            "channel_type": user_message.get("channel_type", "console"),
            "account_id": user_message.get("account_id", self._account_id),
            "session_id": user_message.get("session_id", self._session_id),
            "timestamp": user_message.get("timestamp", 0),
        })
        self._pending_messages.append(user_message)

    @workflow.signal
    async def cancel_session(self) -> None:
        """终止信号 —— 设置 _completed = True，主循环退出。"""
        self._completed = True

    # ══════════════════════════════════════════════════════════════════════
    # Temporal Queries —— 外部查询运行中 Workflow 的状态（只读，不改变状态）
    # ══════════════════════════════════════════════════════════════════════

    @workflow.query
    def get_events(self) -> List[Dict[str, Any]]:
        """返回当前收集的所有事件历史。

        供 TemporalSessionManager 通过 Workflow Query 读取，
        实现不依赖本地存储的会话历史访问。
        """
        return self._events

    @workflow.query
    def get_status(self) -> Dict[str, Any]:
        """返回 Workflow 执行状态快照。

        包含轮次、事件数、完成状态等，用于外部监控和调试。
        """
        return {
            "turns": self._total_turns,
            "completed": self._completed,
            "event_count": len(self._events),
            "max_turns": self._max_turns,
            "account_id": self._account_id,
            "session_id": self._session_id,
        }
