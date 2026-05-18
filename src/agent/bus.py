"""InMemoryMessageBus — pure communication pipe.

No orchestration semantics. Handoff has been removed from this layer.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from .interfaces import MessageBus


class InMemoryMessageBus(MessageBus):
    """Process-local message bus backed by asyncio.Queue.

    Routes messages by capability tag matching.
    """

    def __init__(self) -> None:
        # capability_tag -> list of asyncio.Queue
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._broadcast_queues: list[asyncio.Queue] = []

    async def send(self, target_capability: str, message: Any) -> None:
        """Send a message to all subscribers of a capability."""
        queues = self._subscribers.get(target_capability, [])
        for queue in queues:
            await queue.put(message)

    async def broadcast(self, message: Any, capabilities: list[str] | None = None) -> list[Any]:
        """Broadcast to all (or filtered) subscribers and collect replies.

        For Council mode — sends a message and waits for replies.
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
        """Subscribe to messages for a capability. Yields messages as they arrive."""
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
