"""Test MultiAgentExecutor — orchestrator runner bridge."""

from unittest.mock import AsyncMock

from src.agent.runner import MultiAgentExecutor


def make_response(text):
    """Create a mock ModelResponse."""

    class R:
        content = text
        tool_calls = []
    return R()


class TestMultiAgentExecutor:
    async def test_execute_single_agent_supervisor(self):
        """MultiAgentExecutor with one agent runs orchestration -> synthesizes."""
        pool = AsyncMock()

        call_seq = [0]

        async def mock_generate(messages, model_selector="chat", tools=None, stream=False):
            call_seq[0] += 1
            msg_text = str(messages)

            if call_seq[0] == 1:
                # Planner (via ResourcePoolAdapter)
                return make_response(
                    '{"tasks": [{"task_id": "t1", "goal": "answer question", '
                    '"required_tags": ["default"], "depends_on": []}]}'
                )
            elif call_seq[0] == 2:
                # Agent execution (via LLMAgent)
                return make_response("Paris is the capital of France.")
            elif call_seq[0] == 3:
                # Reviewer (via ResourcePoolAdapter)
                return make_response('{"is_done": true, "reasoning": "done"}')
            else:
                # Synthesizer (via MultiAgentExecutor._synthesize)
                return make_response("The answer is Paris.")

        pool.generate = AsyncMock(side_effect=mock_generate)

        executor = MultiAgentExecutor(
            resource_pool=pool,
            agents_config=[{
                "tag": "default",
                "model_selector": "chat",
                "system_prompt": "You are helpful.",
            }],
            strategy="supervisor",
            max_review_rounds=1,
        )

        content, turns = await executor.execute(
            goal="What is the capital of France?",
            history_events=[],
            memories_text="",
        )

        assert "Paris" in content
        assert turns >= 1

    async def test_council_strategy(self):
        """MultiAgentExecutor with council strategy builds a council orchestrator."""
        pool = AsyncMock()

        call_seq = [0]

        async def mock_generate(messages, **kw):
            call_seq[0] += 1

            if call_seq[0] == 1:
                # Judge (via ResourcePoolAdapter)
                return make_response(
                    '{"verdict": "yes", "reasoning": "all agree", "confidence": 1.0}'
                )
            elif call_seq[0] in (2, 3):
                # Two agent executions
                return make_response("yes")
            else:
                # Synthesizer
                return make_response("Synthesized: yes — proceed.")

        pool.generate = AsyncMock(side_effect=mock_generate)

        executor = MultiAgentExecutor(
            resource_pool=pool,
            agents_config=[
                {"tag": "a", "model_selector": "chat", "system_prompt": "Agent A"},
                {"tag": "b", "model_selector": "chat", "system_prompt": "Agent B"},
            ],
            strategy="council",
        )

        content, turns = await executor.execute(goal="Should we proceed?")
        assert "yes" in content.lower()
        assert turns >= 1

    async def test_synthesize_fallback(self):
        """When synthesis LLM fails, returns raw results."""
        pool = AsyncMock()

        call_seq = [0]

        async def mock_generate(messages, **kw):
            call_seq[0] += 1

            if call_seq[0] == 1:
                # Planner
                return make_response(
                    '{"tasks": [{"task_id": "t1", "goal": "do the task", '
                    '"required_tags": ["default"], "depends_on": []}]}'
                )
            elif call_seq[0] == 2:
                # Agent execution
                return make_response("task result text here")
            elif call_seq[0] == 3:
                # Reviewer
                return make_response('{"is_done": true, "reasoning": "ok"}')
            else:
                # Synthesizer — simulate API error
                raise RuntimeError("Synthesis API error")

        pool.generate = AsyncMock(side_effect=mock_generate)

        executor = MultiAgentExecutor(
            resource_pool=pool,
            agents_config=[{
                "tag": "default",
                "model_selector": "chat",
                "system_prompt": "OK",
            }],
        )

        content, turns = await executor.execute(goal="test")
        # Should fall back to raw results
        assert "task result" in content.lower()
        assert turns >= 1

    async def test_execute_with_history_and_memories(self):
        """Executor runs successfully even with context data."""
        pool = AsyncMock()

        call_seq = [0]

        async def mock_generate(messages, **kw):
            call_seq[0] += 1

            if call_seq[0] == 1:
                return make_response(
                    '{"tasks": [{"task_id": "t1", "goal": "answer", '
                    '"required_tags": ["default"], "depends_on": []}]}'
                )
            elif call_seq[0] == 2:
                return make_response("answer text")
            elif call_seq[0] == 3:
                return make_response('{"is_done": true, "reasoning": "ok"}')
            else:
                return make_response("final synthesis")

        pool.generate = AsyncMock(side_effect=mock_generate)

        executor = MultiAgentExecutor(
            resource_pool=pool,
            agents_config=[{
                "tag": "default",
                "model_selector": "chat",
                "system_prompt": "You are helpful.",
            }],
        )

        content, turns = await executor.execute(
            goal="test goal",
            history_events=[],
            memories_text="User likes Python.",
        )

        assert turns >= 1
        assert len(content) > 0
