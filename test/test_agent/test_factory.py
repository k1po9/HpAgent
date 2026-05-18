"""Test orchestrator factory functions."""

from src.agent.context import ExecutionContext, RuntimeConfig
from src.agent.factory import (
    ResourcePoolAdapter,
    build_council,
    build_supervisor,
    build_workflow,
)
from src.agent.types import (
    CapabilityRequirement,
    Task,
    TaskStatus,
)

from .conftest import make_agent, make_context


# ── ResourcePoolAdapter Tests ──────────────────────────────────────────────────

class TestResourcePoolAdapter:
    async def test_adapter_extracts_content(self):
        """Adapter extracts content from ModelResponse."""
        class MockResponse:
            content = "hello world"

        class MockPool:
            async def generate(self, messages, model_selector, tools, stream):
                return MockResponse()

        adapter = ResourcePoolAdapter(MockPool(), model_selector="chat")
        result = await adapter([{"role": "user", "content": "hi"}])
        assert result == "hello world"

    async def test_adapter_handles_none_content(self):
        """Adapter returns empty string when content is None."""
        class MockResponse:
            content = None

        class MockPool:
            async def generate(self, messages, model_selector, tools, stream):
                return MockResponse()

        adapter = ResourcePoolAdapter(MockPool())
        result = await adapter([{"role": "user", "content": "hi"}])
        assert result == ""


# ── Factory Tests ─────────────────────────────────────────────────────────────

class TestBuildSupervisor:
    async def test_builds_runnable_orchestrator(self):
        """build_supervisor returns an orchestrator that can run."""
        async def mock_llm(messages, tools=None):
            return '{"tasks": [{"task_id": "t1", "goal": "test", "required_tags": ["default"], "depends_on": []}]}'

        agents = {"default": make_agent()}
        orch = build_supervisor(call_llm=mock_llm, agents=agents, max_review_rounds=1)
        results = await orch.run("test goal", make_context())

        assert "t1" in results
        assert results["t1"].status == TaskStatus.COMPLETED


class TestBuildCouncil:
    async def test_builds_council_with_real_judge(self):
        """build_council with real judge runs voting agents."""
        async def mock_llm(messages, tools=None):
            return '{"verdict": "blue", "reasoning": "consensus", "confidence": 1.0}'

        agents = {
            "a": make_agent(output="blue", tags={"a"}),
            "b": make_agent(output="blue", tags={"b"}),
        }
        orch = build_council(call_llm=mock_llm, agents=agents, council_name="c")
        results = await orch.run("pick", make_context())

        assert "c_verdict" in results
        assert results["c_verdict"].status == TaskStatus.COMPLETED
        assert results["c_verdict"].output["verdict"] == "blue"

    async def test_builds_council_with_majority_judge(self):
        """build_council with use_real_judge=False uses MajorityJudge."""
        agents = {
            "a": make_agent(output="yes", tags={"a"}),
            "b": make_agent(output="yes", tags={"b"}),
            "c": make_agent(output="no", tags={"c"}),
        }
        orch = build_council(
            call_llm=None, agents=agents, council_name="c", use_real_judge=False,
        )
        results = await orch.run("vote", make_context())

        verdict = results["c_verdict"]
        assert verdict.output["verdict"] == "yes"
        assert verdict.output["votes"] == 2


class TestBuildWorkflow:
    async def test_builds_workflow_from_dag(self):
        """build_workflow creates a DAG orchestrator."""
        t1 = Task(task_id="a", goal="first",
                  required_capability=CapabilityRequirement(required_tags={"default"}))
        t2 = Task(task_id="b", goal="second",
                  required_capability=CapabilityRequirement(required_tags={"default"}))
        agents = {"default": make_agent()}
        orch = build_workflow(
            dag_tasks={"a": t1, "b": t2},
            dag_dependencies={"b": ["a"]},
            agents=agents,
        )
        results = await orch.run("test", make_context())

        assert "a" in results
        assert "b" in results
        assert results["a"].status == TaskStatus.COMPLETED
        assert results["b"].status == TaskStatus.COMPLETED
