"""Test SupervisorControlStrategy — LLM dynamic planning + review."""

from src.agent.bus import InMemoryMessageBus
from src.agent.orchestrator import Orchestrator
from src.agent.registry import InMemoryAgentRegistry
from src.agent.strategies import (
    StubLLMPlanner,
    StubLLMReviewer,
    SupervisorControlStrategy,
)
from src.agent.types import (
    CapabilityRequirement,
    Task,
    TaskStatus,
)

from .conftest import make_agent, make_context


class TestSupervisorStrategy:
    """Test Supervisor mode: plan → execute → review → continue/terminate."""

    async def test_single_round_planning(self):
        """Planner returns 2 tasks, reviewer says done → 2 results."""
        t1 = Task(task_id="s1", goal="step 1",
                  required_capability=CapabilityRequirement(required_tags={"default"}))
        t2 = Task(task_id="s2", goal="step 2",
                  required_capability=CapabilityRequirement(required_tags={"default"}))

        planner = StubLLMPlanner({"test": ([t1, t2], {})})
        reviewer = StubLLMReviewer([(True, None)])  # Done after first review

        strategy = SupervisorControlStrategy(planner, reviewer)
        registry = InMemoryAgentRegistry()
        registry.register_direct("default", make_agent())
        bus = InMemoryMessageBus()
        orch = Orchestrator(strategy, registry, bus)

        results = await orch.run("test", make_context())
        assert len(results) == 2
        assert results["s1"].status == TaskStatus.COMPLETED
        assert results["s2"].status == TaskStatus.COMPLETED

    async def test_multi_round_planning(self):
        """Round 1: 1 task → Round 2: reviewer returns 1 more task → done."""
        t1 = Task(task_id="r1", goal="round 1",
                  required_capability=CapabilityRequirement(required_tags={"default"}))
        t2 = Task(task_id="r2", goal="round 2",
                  required_capability=CapabilityRequirement(required_tags={"default"}))

        planner = StubLLMPlanner({"test": ([t1], {})})
        # Round 1: not done, add t2. Round 2: done.
        reviewer = StubLLMReviewer([(False, [t2]), (True, None)])

        strategy = SupervisorControlStrategy(planner, reviewer)
        registry = InMemoryAgentRegistry()
        registry.register_direct("default", make_agent())
        bus = InMemoryMessageBus()
        orch = Orchestrator(strategy, registry, bus)

        results = await orch.run("test", make_context())
        assert "r1" in results
        assert "r2" in results
        assert results["r1"].status == TaskStatus.COMPLETED
        assert results["r2"].status == TaskStatus.COMPLETED

    async def test_reviewer_terminates_early(self):
        """Reviewer says done immediately, even though planner had tasks."""
        t1 = Task(task_id="t1", goal="not needed",
                  required_capability=CapabilityRequirement(required_tags={"default"}))

        planner = StubLLMPlanner({"test": ([t1], {})})
        reviewer = StubLLMReviewer([(True, None)])  # Done immediately

        strategy = SupervisorControlStrategy(planner, reviewer)
        registry = InMemoryAgentRegistry()
        registry.register_direct("default", make_agent())
        bus = InMemoryMessageBus()
        orch = Orchestrator(strategy, registry, bus)

        results = await orch.run("test", make_context())
        assert "t1" in results
        assert results["t1"].status == TaskStatus.COMPLETED
