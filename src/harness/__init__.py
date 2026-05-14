"""
Harness —— 无状态大脑层。

在"手脑分离"架构中，Harness 是"大脑"，通过 HarnessRunner 无状态协调:
  1. SessionStore → 会话事件读写 + 长期记忆（Redis + Hindsight）
  2. ContextBuilder → 事件历史 → LLM messages
  3. ResourcePool → 模型调用（退避链）
  4. SandboxManager → 工具执行（nsjail 隔离）
  5. ChannelRouter → 多渠道响应路由

HarnessRunner 是 Temporal Activities 唯一的交互对象。
Temporal Workflow 只做编排（循环控制 + 信号），不持有业务数据。

模块结构：
  - runner.py: HarnessRunner —— 无状态协调器（agentic loop）
  - activities.py: 3 个 Temporal Activity 薄封装
  - context_builder.py: HarnessContextBuilder —— 事件 → LLM messages 转换器
"""
from .activities import (
    inject,
    process_turn_activity,
    archive_session_activity,
    reflect_activity,
)
from .context_builder import HarnessContextBuilder
from .runner import HarnessRunner

__all__ = [
    "HarnessRunner",
    "HarnessContextBuilder",
    "inject",
    "process_turn_activity",
    "archive_session_activity",
    "reflect_activity",
]
