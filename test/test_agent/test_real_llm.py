"""Test RealLLMPlanner, RealLLMReviewer, RealLLMJudge with mock LLM calls."""

from src.agent.context import ExecutionContext, RuntimeConfig
from src.agent.strategies import (
    RealLLMJudge,
    RealLLMPlanner,
    RealLLMReviewer,
)
from src.agent.types import (
    CapabilityRequirement,
    Task,
    TaskResult,
    TaskStatus,
)


def make_context():
    return ExecutionContext(config=RuntimeConfig(timeout_seconds=5))


def make_mock_llm(response_text):
    """Create an async call_llm that returns a fixed response."""
    async def mock(messages, tools=None):
        return response_text
    return mock


# ── Planner Tests ─────────────────────────────────────────────────────────────

class TestRealLLMPlanner:
    async def test_parses_simple_plan(self):
        """LLM returns a 2-task plan → planner extracts tasks + deps."""
        response = (
            '{"tasks": ['
            '{"task_id": "research", "goal": "research topic", '
            '"required_tags": ["search"], "depends_on": []},'
            '{"task_id": "write", "goal": "write report", '
            '"required_tags": ["code"], "depends_on": ["research"]}'
            ']}'
        )
        planner = RealLLMPlanner(call_llm=make_mock_llm(response))
        tasks, deps = await planner.plan("write a report", make_context())

        assert len(tasks) == 2
        assert tasks[0].task_id == "research"
        assert tasks[0].required_capability.required_tags == {"search"}
        assert tasks[1].task_id == "write"
        assert deps == {"write": ["research"]}

    async def test_parses_markdown_fenced_json(self):
        """LLM returns JSON inside ``` fences → still parsed."""
        response = (
            '```json\n'
            '{"tasks": [{"task_id": "t1", "goal": "do it", '
            '"required_tags": ["default"], "depends_on": []}]}\n'
            '```'
        )
        planner = RealLLMPlanner(call_llm=make_mock_llm(response))
        tasks, deps = await planner.plan("do it", make_context())

        assert len(tasks) == 1
        assert tasks[0].task_id == "t1"

    async def test_fallback_on_llm_failure(self):
        """LLM call fails → fallback to single default task."""
        async def failing_llm(messages, tools=None):
            raise RuntimeError("LLM down")
        planner = RealLLMPlanner(call_llm=failing_llm)
        tasks, deps = await planner.plan("important goal", make_context())

        assert len(tasks) == 1
        assert tasks[0].task_id == "fallback_1"
        assert tasks[0].goal == "important goal"
        assert tasks[0].required_capability.required_tags == {"default"}
        assert deps == {}

    async def test_fallback_on_malformed_json(self):
        """LLM returns invalid JSON → fallback to single task."""
        planner = RealLLMPlanner(call_llm=make_mock_llm("not json at all"))
        tasks, deps = await planner.plan("goal", make_context())

        assert len(tasks) == 1
        assert tasks[0].task_id == "fallback_1"

    async def test_empty_tasks_list_triggers_fallback(self):
        """LLM returns empty tasks array → fallback task."""
        planner = RealLLMPlanner(call_llm=make_mock_llm('{"tasks": []}'))
        tasks, deps = await planner.plan("goal", make_context())

        assert len(tasks) == 1
        assert tasks[0].task_id == "fallback_1"

    async def test_custom_default_tags(self):
        """Custom default_tags are used when task omits required_tags."""
        response = '{"tasks": [{"task_id": "t1", "goal": "do", "depends_on": []}]}'
        planner = RealLLMPlanner(
            call_llm=make_mock_llm(response),
            default_tags={"custom"},
        )
        tasks, deps = await planner.plan("goal", make_context())
        assert tasks[0].required_capability.required_tags == {"custom"}


# ── Reviewer Tests ────────────────────────────────────────────────────────────

class TestRealLLMReviewer:
    async def test_reviewer_says_done(self):
        """LLM says is_done=true → reviewer returns (True, None)."""
        reviewer = RealLLMReviewer(
            call_llm=make_mock_llm('{"is_done": true, "reasoning": "all good"}')
        )
        completed = {
            "t1": TaskResult(task_id="t1", status=TaskStatus.COMPLETED, output="result"),
        }
        is_done, new_tasks = await reviewer.review(completed, make_context())

        assert is_done is True
        assert new_tasks is None

    async def test_reviewer_injects_new_tasks(self):
        """LLM says not done + provides new tasks."""
        response = (
            '{"is_done": false, "reasoning": "need more", '
            '"new_tasks": [{"task_id": "next", "goal": "continue", '
            '"required_tags": ["default"], "depends_on": []}]}'
        )
        reviewer = RealLLMReviewer(call_llm=make_mock_llm(response))
        completed = {"t1": TaskResult(task_id="t1", status=TaskStatus.COMPLETED, output="partial")}
        is_done, new_tasks = await reviewer.review(completed, make_context())

        assert is_done is False
        assert new_tasks is not None
        assert len(new_tasks) == 1
        assert new_tasks[0].task_id == "next"

    async def test_fallback_done_on_llm_failure(self):
        """LLM call fails → assume done (safe default)."""
        async def failing_llm(messages, tools=None):
            raise RuntimeError("LLM down")
        reviewer = RealLLMReviewer(call_llm=failing_llm)
        completed = {"t1": TaskResult(task_id="t1", status=TaskStatus.COMPLETED, output="ok")}
        is_done, new_tasks = await reviewer.review(completed, make_context())

        assert is_done is True
        assert new_tasks is None

    async def test_max_rounds_forces_done(self):
        """After max_rounds, reviewer returns done regardless of LLM."""
        response = '{"is_done": false, "new_tasks": [{"task_id": "n", "goal": "g", "required_tags": ["default"], "depends_on": []}]}'
        reviewer = RealLLMReviewer(call_llm=make_mock_llm(response), max_rounds=2)
        completed = {"t1": TaskResult(task_id="t1", status=TaskStatus.COMPLETED, output="ok")}

        # Round 1: not done, returns new tasks
        is_done1, tasks1 = await reviewer.review(completed, make_context())
        assert is_done1 is False
        assert tasks1 is not None

        # Round 2: max rounds hit → forced done
        is_done2, tasks2 = await reviewer.review(completed, make_context())
        assert is_done2 is True
        assert tasks2 is None


# ── Judge Tests ───────────────────────────────────────────────────────────────

class TestRealLLMJudge:
    async def test_judge_selects_best(self):
        """LLM judge picks one answer as best."""
        response = (
            '{"verdict": "answer_b", "reasoning": "more thorough", '
            '"confidence": 0.9}'
        )
        judge = RealLLMJudge(call_llm=make_mock_llm(response))
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.COMPLETED, output="answer_a"),
            "b": TaskResult(task_id="b", status=TaskStatus.COMPLETED, output="answer_b"),
        }
        verdict = await judge.judge(results, make_context())

        assert verdict["verdict"] == "answer_b"
        assert verdict["confidence"] == 0.9
        assert "results" in verdict

    async def test_judge_no_consensus_when_empty(self):
        """No successful results → no_consensus verdict."""
        judge = RealLLMJudge(call_llm=make_mock_llm("{}"))
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.FAILED, output=None),
        }
        verdict = await judge.judge(results, make_context())

        assert verdict["verdict"] == "no_consensus"

    async def test_fallback_to_majority_on_llm_failure(self):
        """LLM fails → fallback to majority vote."""
        async def failing_llm(messages, tools=None):
            raise RuntimeError("LLM down")
        judge = RealLLMJudge(call_llm=failing_llm)
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.COMPLETED, output="x"),
            "b": TaskResult(task_id="b", status=TaskStatus.COMPLETED, output="x"),
            "c": TaskResult(task_id="c", status=TaskStatus.COMPLETED, output="y"),
        }
        verdict = await judge.judge(results, make_context())

        assert verdict["verdict"] == "x"
        assert verdict["votes"] == 2

    async def test_handles_markdown_fenced_json(self):
        """Judge handles markdown-fenced JSON response."""
        response = (
            '```\n'
            '{"verdict": "synthesized", "reasoning": "combined", '
            '"confidence": 0.85}\n'
            '```'
        )
        judge = RealLLMJudge(call_llm=make_mock_llm(response))
        results = {
            "a": TaskResult(task_id="a", status=TaskStatus.COMPLETED, output="x"),
        }
        verdict = await judge.judge(results, make_context())

        assert verdict["verdict"] == "synthesized"
        assert verdict["confidence"] == 0.85
