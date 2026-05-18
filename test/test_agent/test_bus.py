"""Test InMemoryMessageBus."""

import asyncio
import pytest
from src.agent.bus import InMemoryMessageBus


class TestInMemoryMessageBus:
    async def test_send_to_subscriber(self):
        bus = InMemoryMessageBus()
        received = []

        async def listener():
            async for msg in bus.listen("code"):
                received.append(msg)
                break

        listener_task = asyncio.ensure_future(listener())
        await asyncio.sleep(0.01)  # let listener subscribe
        await bus.send("code", {"type": "test"})
        await asyncio.wait_for(listener_task, timeout=1.0)
        assert len(received) == 1
        assert received[0] == {"type": "test"}

    async def test_broadcast_to_multiple(self):
        bus = InMemoryMessageBus()
        received_1 = []
        received_2 = []

        async def listener_1():
            async for msg in bus.listen("code"):
                received_1.append(msg)
                break

        async def listener_2():
            async for msg in bus.listen("code"):
                received_2.append(msg)
                break

        t1 = asyncio.ensure_future(listener_1())
        t2 = asyncio.ensure_future(listener_2())
        await asyncio.sleep(0.01)

        # Send to both listeners (broadcast without waiting for replies)
        await bus.send("code", {"type": "broadcast"})

        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)
        assert len(received_1) == 1
        assert len(received_2) == 1
