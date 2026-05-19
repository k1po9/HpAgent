"""InMemoryMessageBus — 纯通信通道。

不包含编排语义。本层已移除交接（handoff）逻辑。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from .interfaces import MessageBus


class InMemoryMessageBus(MessageBus):
    """进程内消息总线，基于 asyncio.Queue 实现。

    通过能力（capability）标签匹配来路由消息。
    """

    def __init__(self) -> None:
        # capability_tag -> list of asyncio.Queue
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._broadcast_queues: list[asyncio.Queue] = []

    async def send(self, target_capability: str, message: Any) -> None:
        """向所有订阅特定能力的队列发送消息。"""
        queues = self._subscribers.get(target_capability, [])
        for queue in queues:
            await queue.put(message)

    async def broadcast(self, message: Any, capabilities: list[str] | None = None) -> list[Any]:
        """广播消息给所有（或指定能力的）订阅者并收集回复。

        在 Council 模式下使用 —— 发送消息并等待回复。
        """
        if capabilities:
            targets = []
            for cap in capabilities:
                targets.extend(self._subscribers.get(cap, []))
        else:
            targets = list(self._broadcast_queues)

        replies: list[Any] = []
        for queue in targets:
            await queue.put(message)
            try:
                reply = await asyncio.wait_for(queue.get(), timeout=5.0)
                replies.append(reply)
            except asyncio.TimeoutError:
                pass
        return replies

    async def listen(self, agent_capability: str) -> AsyncIterator[Any]:
        """订阅某能力的消息。按到达顺序产出消息。"""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(agent_capability, []).append(queue)
        self._broadcast_queues.append(queue)
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield message
                except asyncio.TimeoutError:
                    # Allow checking for cancellation
                    await asyncio.sleep(0)
        finally:
            if queue in self._broadcast_queues:
                self._broadcast_queues.remove(queue)
            for queues in self._subscribers.values():
                if queue in queues:
                    queues.remove(queue)
