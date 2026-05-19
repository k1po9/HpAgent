"""补偿注册表 —— 基于实例，不是全局单例。

设计决策：每个 Orchestrator 实例拥有自己的 CompensationRegistry，
以防在递归组合时出现 task_type 冲突。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .context import ExecutionContext
from .types import Task


class CompensationHandler(ABC):
    """用于回滚已完成任务副作用的处理器。"""

    @abstractmethod
    async def compensate(self, task: Task, context: ExecutionContext) -> None:
        """为指定任务执行补偿逻辑。"""
        ...


class CompensationRegistry:
    """每个 Orchestrator 实例的补偿处理器注册表。

    不是全局单例——每个 Orchestrator 都有自己的实例。
    这可以避免在递归组合中出现 task_type 冲突。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, CompensationHandler] = {}

    def register(self, task_type: str, handler: CompensationHandler) -> None:
        """为某个任务类型注册补偿处理器。"""
        self._handlers[task_type] = handler

    def get(self, task_type: str) -> Optional[CompensationHandler]:
        """获取指定任务类型的补偿处理器。"""
        return self._handlers.get(task_type)

