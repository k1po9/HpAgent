"""Test LLMAgent — lightweight ResourcePool-backed BaseAgent."""

from unittest.mock import AsyncMock

from src.agent.context import ExecutionContext, RuntimeConfig
from src.agent.llm_agent import LLMAgent
from src.agent.types import (
    CapabilitySpec,
    Task,
    TaskStatus,
)


def make_context():
    return ExecutionContext(config=RuntimeConfig(timeout_seconds=5))


async def make_mock_pool(response_text="hello", tool_calls_resp=None):
    """Create a mock ResourcePool with controllable generate()."""

    class MockResponse:
        content = response_text
        tool_calls = tool_calls_resp or []

    pool = AsyncMock()
    pool.generate = AsyncMock(return_value=MockResponse())
    return pool


class TestLLMAgent:
    async def test_basic_execution(self):
        """LLMAgent calls ResourcePool and returns TaskResult."""
        pool = await make_mock_pool(response_text="result text")
        agent = LLMAgent(
            resource_pool=pool,
            model_selector="chat",
            system_prompt="You are helpful.",
        )
        task = Task(task_id="t1", goal="what is 2+2?")
        result = await agent.execute(task, make_context())

        assert result.status == TaskStatus.COMPLETED
        assert result.output == "result text"
        pool.generate.assert_called_once()

    async def test_system_prompt_in_messages(self):
        """System prompt is first message."""
        pool = await make_mock_pool()
        agent = LLMAgent(
            resource_pool=pool,
            system_prompt="Custom system prompt",
        )
        task = Task(task_id="t1", goal="test")
        await agent.execute(task, make_context())

        messages = pool.generate.call_args[1]["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Custom system prompt"

    async def test_context_injection(self):
        """task.input_data.memories is injected as context."""
        pool = await make_mock_pool()
        agent = LLMAgent(resource_pool=pool)
        task = Task(
            task_id="t1",
            goal="test",
            input_data={"memories": "User prefers Python"},
        )
        await agent.execute(task, make_context())

        messages = pool.generate.call_args[1]["messages"]
        contents = " ".join(m["content"] for m in messages)
        assert "User prefers Python" in contents

    async def test_history_formatting(self):
        """History events are formatted into context text."""

        class MockEvent:
            def __init__(self, etype, text):
                self.event_type = etype
                self.content = {"text": text}

        pool = await make_mock_pool()
        agent = LLMAgent(resource_pool=pool)
        task = Task(
            task_id="t1",
            goal="current question",
            input_data={
                "history": [
                    MockEvent("USER_MESSAGE", "hello"),
                    MockEvent("MODEL_MESSAGE", "hi there"),
                ],
            },
        )
        await agent.execute(task, make_context())

        messages = pool.generate.call_args[1]["messages"]
        contents = " ".join(m["content"] for m in messages)
        assert "hello" in contents
        assert "hi there" in contents

    async def test_capability_spec(self):
        """LLMAgent exposes its capability spec."""
        spec = CapabilitySpec(tags={"analyst"}, priority=1)
        agent = LLMAgent(resource_pool=AsyncMock(), capability_spec=spec)
        assert agent.capability.tags == {"analyst"}
        assert agent.capability.priority == 1

    async def test_default_capability(self):
        """Default capability spec has 'default' tag."""
        agent = LLMAgent(resource_pool=AsyncMock())
        assert agent.capability.tags == {"default"}

    async def test_failure_returns_failed_result(self):
        """LLM call exception -> FAILED TaskResult."""
        pool = AsyncMock()
        pool.generate = AsyncMock(side_effect=RuntimeError("API down"))
        agent = LLMAgent(resource_pool=pool)
        task = Task(task_id="t1", goal="test")

        result = await agent.execute(task, make_context())
        assert result.status == TaskStatus.FAILED
        assert result.error is not None
        assert result.error.type == "RuntimeError"
        assert "API down" in result.error.message

    async def test_tool_loop_without_executor(self):
        """When tools are set but no tool_executor, returns content directly."""
        pool = await make_mock_pool(response_text="I'll search that")
        agent = LLMAgent(
            resource_pool=pool,
            tools=[{"type": "function", "function": {"name": "search"}}],
            tool_executor=None,
        )
        task = Task(task_id="t1", goal="search something")
        result = await agent.execute(task, make_context())

        assert result.status == TaskStatus.COMPLETED
        assert result.output == "I'll search that"
        assert pool.generate.call_count == 1

    async def test_tool_loop_with_executor(self):
        """Mini ReAct loop: model -> tool_calls -> execute -> model -> done."""
        call_count = [0]

        pool = AsyncMock()

        async def mock_generate(**kw):
            call_count[0] += 1
            if call_count[0] == 1:

                class TC:
                    id = "tc1"
                    name = "search"
                    arguments = {"q": "test"}
                resp = type("R", (), {
                    "content": "using tool",
                    "tool_calls": [TC()],
                })()
            else:
                resp = type("R", (), {
                    "content": "done",
                    "tool_calls": [],
                })()
            return resp

        pool.generate = AsyncMock(side_effect=mock_generate)

        async def tool_exec(name, args):
            return {"output": f"Result for {args.get('q', '')}"}

        agent = LLMAgent(
            resource_pool=pool,
            tools=[{"type": "function", "function": {"name": "search"}}],
            tool_executor=tool_exec,
            max_tool_turns=5,
        )
        task = Task(task_id="t1", goal="search something")
        result = await agent.execute(task, make_context())

        assert result.status == TaskStatus.COMPLETED
        assert result.output == "done"
        assert pool.generate.call_count == 2

    async def test_max_tool_turns_limit(self):
        """After max_tool_turns, returns last content even if tool_calls continue."""

        class MockResponse:
            content = "still searching"
            tool_calls = [
                type("TC", (), {"id": "x", "name": "s", "arguments": {}})()
            ]

        pool = AsyncMock()
        pool.generate = AsyncMock(return_value=MockResponse())

        async def tool_exec(name, args):
            return {"output": "stub"}

        agent = LLMAgent(
            resource_pool=pool,
            tools=[{"type": "function", "function": {"name": "s"}}],
            tool_executor=tool_exec,
            max_tool_turns=2,
        )
        task = Task(task_id="t1", goal="test")
        result = await agent.execute(task, make_context())

        assert result.status == TaskStatus.COMPLETED
        assert pool.generate.call_count == 2
