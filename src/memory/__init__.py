"""
Memory 模块 —— Hindsight 记忆系统的 Python 客户端封装。

提供 3 个核心能力:
  1. retain:  从对话中提取并存储记忆（LLM 驱动）
  2. recall:  多路语义检索相关记忆（快速，无 LLM）
  3. reflect: 深度记忆推理与知识抽象（定时触发）

设计原则:
  - 所有方法均为可选增强：Hindsight 不可用时降级为空操作
  - recall 在关键路径上（模型调用前），有超时控制
  - retain 异步触发（每轮后），不阻塞 agentic loop
  - reflect 通过 Temporal Schedule 定期调度
"""
from memory.hindsight_client import HindsightClient, MemoryItem

__all__ = ["HindsightClient", "MemoryItem"]
