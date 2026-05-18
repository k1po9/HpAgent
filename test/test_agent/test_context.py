"""Test ExecutionContext and SharedMemory concurrent safety."""

import asyncio
import pytest
from src.agent.context import ExecutionContext, RuntimeConfig, SessionState, SharedMemory


class TestSharedMemory:
    async def test_set_and_get(self):
        sm = SharedMemory()
        await sm.set("ns1", "key1", "value1")
        result = await sm.get("ns1", "key1")
        assert result == "value1"

    async def test_missing_key_returns_none(self):
        sm = SharedMemory()
        result = await sm.get("ns1", "nonexistent")
        assert result is None

    async def test_namespace_isolation(self):
        sm = SharedMemory()
        await sm.set("ns1", "key", "val1")
        await sm.set("ns2", "key", "val2")
        assert await sm.get("ns1", "key") == "val1"
        assert await sm.get("ns2", "key") == "val2"

    async def test_overwrite(self):
        sm = SharedMemory()
        await sm.set("ns", "key", "old")
        await sm.set("ns", "key", "new")
        assert await sm.get("ns", "key") == "new"

    async def test_delete(self):
        sm = SharedMemory()
        await sm.set("ns", "key", "val")
        assert await sm.delete("ns", "key") is True
        assert await sm.get("ns", "key") is None
        assert await sm.delete("ns", "nonexistent") is False

    async def test_cas_success(self):
        sm = SharedMemory()
        await sm.set("ns", "key", "old")
        result = await sm.compare_and_set("ns", "key", "old", "new")
        assert result is True
        assert await sm.get("ns", "key") == "new"

    async def test_cas_failure_on_mismatch(self):
        sm = SharedMemory()
        await sm.set("ns", "key", "actual")
        result = await sm.compare_and_set("ns", "key", "wrong_expected", "new")
        assert result is False
        assert await sm.get("ns", "key") == "actual"

    async def test_concurrent_writes(self):
        """Multiple concurrent writes should not cause data loss."""
        sm = SharedMemory()
        count = 50

        async def writer(i: int):
            await sm.set("ns", f"key_{i}", f"value_{i}")

        await asyncio.gather(*[writer(i) for i in range(count)])

        for i in range(count):
            assert await sm.get("ns", f"key_{i}") == f"value_{i}"

    async def test_concurrent_cas(self):
        """CAS operations under lock are serialized — each one sees the latest value."""
        sm = SharedMemory()
        await sm.set("ns", "counter", 0)
        successes = 0

        async def cas_increment():
            nonlocal successes
            # Read-then-CAS: under asyncio cooperative scheduling, each task
            # runs read+CAS sequentially before yielding to the next task
            current = await sm.get("ns", "counter")
            if await sm.compare_and_set("ns", "counter", current, current + 1):
                successes += 1

        await asyncio.gather(*[cas_increment() for _ in range(10)])
        # In asyncio's cooperative model, each task completes read+CAS
        # before the next task starts, so all 10 succeed sequentially
        assert successes == 10
        assert await sm.get("ns", "counter") == 10


class TestExecutionContext:
    def test_default_construction(self):
        ctx = ExecutionContext()
        assert ctx.session.user_id == ""
        assert ctx.config.timeout_seconds == 300
        assert ctx.trace_id != ""

    def test_custom_config(self):
        ctx = ExecutionContext(
            session=SessionState(user_id="user_1"),
            config=RuntimeConfig(timeout_seconds=60, max_retries=5, model_name="claude"),
            trace_id="trace_abc",
        )
        assert ctx.session.user_id == "user_1"
        assert ctx.config.timeout_seconds == 60
        assert ctx.config.max_retries == 5
        assert ctx.config.model_name == "claude"
        assert ctx.trace_id == "trace_abc"

    def test_unique_trace_ids(self):
        ctx1 = ExecutionContext()
        ctx2 = ExecutionContext()
        assert ctx1.trace_id != ctx2.trace_id
