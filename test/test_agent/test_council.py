"""Test CouncilControlStrategy — parallel same-task + judge verdict."""

from src.agent.bus import InMemoryMessageBus
from src.agent.orchestrator import Orchestrator
from src.agent.registry import InMemoryAgentRegistry
from src.agent.strategies import (
    CouncilControlStrategy,
    MajorityJudge,
)
from src.agent.types import (
    CapabilityRequirement,
    CapabilitySpec,
    Task,
    TaskResult,
    TaskStatus,
)

from .conftest import make_agent, make_context


class TestCouncilStrategy:
    """Test Council mode: N agents vote, judge decides."""

    async def test_three_agent_council(self):
        """3 agents with different outputs → majority judge picks winner."""
        caps = [
            CapabilityRequirement(required_tags={"agent_a"}),
            CapabilityRequirement(required_tags={"agent_b"}),
            CapabilityRequirement(required_tags={"agent_c"}),
        ]
        judge = MajorityJudge()
        strategy = CouncilControlStrategy(caps, judge, council_name="test_council")

        registry = InMemoryAgentRegistry()
        registry.register_direct("agent_a", make_agent(output="option_1", tags={"agent_a"}))
        registry.register_direct("agent_b", make_agent(output="option_1", tags={"agent_b"}))
        registry.register_direct("agent_c", make_agent(output="option_2", tags={"agent_c"}))

        bus = InMemoryMessageBus()
        orch = Orchestrator(strategy, registry, bus)

        results = await orch.run("decide", make_context())
        assert "test_council_verdict" in results
        verdict = results["test_council_verdict"]
        assert verdict.status == TaskStatus.COMPLETED
        # Majority: option_1 has 2 votes, option_2 has 1
        assert verdict.output["verdict"] == "option_1"
        assert verdict.output["votes"] == 2

    async def test_council_partial_failure(self):
        """1 of 3 agents fails → judge uses remaining 2."""
        caps = [
            CapabilityRequirement(required_tags={"a"}),
            CapabilityRequirement(required_tags={"b"}),
            CapabilityRequirement(required_tags={"c"}),
        ]
        judge = MajorityJudge()
        strategy = CouncilControlStrategy(caps, judge, council_name="test_council")

        registry = InMemoryAgentRegistry()
        registry.register_direct("a", make_agent(output="x", tags={"a"}))
        registry.register_direct("b", make_agent(output="x", tags={"b"}))
        # Agent C fails
        from src.agent.interfaces import BaseAgent
        class FailAgent(BaseAgent):
            @property
            def capability(self) -> CapabilitySpec:
                return CapabilitySpec(tags={"c"})
            async def execute(self, task, context):
                from src.agent.types import ErrorInfo
                return TaskResult(task_id=task.task_id, status=TaskStatus.FAILED,
                                  error=ErrorInfo(type="TestFailure", message="simulated"))
        registry.register_direct("c", FailAgent())

        bus = InMemoryMessageBus()
        orch = Orchestrator(strategy, registry, bus)

        results = await orch.run("decide", make_context())
        assert "test_council_verdict" in results  # default council_name
        verdict = results["test_council_verdict"]
        assert verdict.status == TaskStatus.COMPLETED
        assert verdict.output["verdict"] == "x"
        assert verdict.output["votes"] == 2
        assert verdict.output["total"] == 2  # only 2 successful

    async def test_council_all_fail(self):
        """All agents fail → no consensus."""
        caps = [
            CapabilityRequirement(required_tags={"a"}),
            CapabilityRequirement(required_tags={"b"}),
        ]
        judge = MajorityJudge()
        strategy = CouncilControlStrategy(caps, judge, council_name="test_council")

        registry = InMemoryAgentRegistry()
        from src.agent.interfaces import BaseAgent
        class FailAgent(BaseAgent):
            @property
            def capability(self) -> CapabilitySpec:
                return CapabilitySpec(tags={"a"})
            async def execute(self, task, context):
                return TaskResult(task_id=task.task_id, status=TaskStatus.FAILED)

        registry.register_direct("a", FailAgent())
        registry.register_direct("b", FailAgent())  # Same class, both fail

        bus = InMemoryMessageBus()
        orch = Orchestrator(strategy, registry, bus)

        results = await orch.run("decide", make_context())
        verdict = results["test_council_verdict"]
        assert verdict.status == TaskStatus.FAILED
        assert verdict.output["verdict"] == "no_consensus"
