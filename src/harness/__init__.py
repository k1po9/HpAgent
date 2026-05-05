"""
Harness —— 无状态大脑层。

在"手脑分离"架构中，Harness 是"大脑"，负责：
  1. 构建 LLM 上下文（ContextBuilder：事件历史 → messages 列表）
  2. 调用模型（call_model_activity → ResourcePool.generate）
  3. 执行工具（execute_tool_activity → SandboxManager）
  4. 发送响应（send_response_activity → ChannelRouter）

所有 Harness 操作都通过 Temporal Activities 暴露给 Workflow，
Activities 是无状态的 —— 依赖在 Worker 启动时通过 inject() 注入。

模块结构：
  - activities.py: 5 个 Temporal Activity 函数（构建上下文 / 获取工具 / 调用模型 / 执行工具 / 发送响应）
  - context_builder.py: HarnessContextBuilder —— 事件历史 → LLM messages 的转换器
"""
from .activities import (
    inject,
    build_context_activity,
    get_available_tools_activity,
    call_model_activity,
    execute_tool_activity,
    send_response_activity,
)
from .context_builder import HarnessContextBuilder

__all__ = [
    "inject",
    "build_context_activity",
    "get_available_tools_activity",
    "call_model_activity",
    "execute_tool_activity",
    "send_response_activity",
    "HarnessContextBuilder",
]
