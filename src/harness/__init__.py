"""
Harness —— 回合编排层。

在当前手脑分离架构中，TurnOrchestrator 是一轮对话的流程导演：
它串起记忆、上下文、大脑、行动运行时和回复服务，但不亲自承担这些组件的内部职责。

主要协作对象：
  1. SessionStore / Hindsight → 会话事件读写、长期记忆、反思
  2. HarnessContextBuilder → 事件历史和记忆 → LLM messages
  3. BrainEngine → 模型推理、HyDE 改写、模型输入快照
  4. ActionRuntime → 工具选择、工具执行、工具审计
  5. ReplyService → 渠道路由、最终回复、工具进度提示

TurnOrchestrator 是 Temporal Activities 的主要交互对象。
Temporal Workflow 只做时间和信号编排，不持有业务数据。

模块结构：
  - runner.py: TurnOrchestrator —— 单轮对话流程导演（agentic loop）
  - activities.py: Temporal Activity 薄封装
  - context_builder.py: HarnessContextBuilder —— 事件 → LLM messages 转换器
"""
from .activities import (
    inject,
    process_turn_activity,
    archive_session_activity,
    reflect_activity,
)
from .context_builder import HarnessContextBuilder
from .runner import TurnOrchestrator

__all__ = [
    "TurnOrchestrator",
    "HarnessContextBuilder",
    "inject",
    "process_turn_activity",
    "archive_session_activity",
    "reflect_activity",
]
