"""Test OrchestratorAsAgent — recursive composition."""

from src.agent.bus import InMemoryMessageBus
from src.agent.composite import OrchestratorAsAgent
from src.agent.orchestrator import Orchestrator
from src.agent.registry import InMemoryAgentRegistry
from src.agent.strategies import ResultAggregator
from src.agent.types import (
    BatchOutcome,
    CapabilityRequirement,
    CapabilitySpec,
    ExecutionPlan,
    Task,
    TaskResult,
    TaskStatus,
)

from .conftest import make_agent, make_context


class TestOrchestratorAsAgent:
    """Test Orchestrator wrapped as an Agent."""

    async def test_single_layer_composition(self):
        """OrchestratorAsAgent can be used as a plain Agent."""
        # Create a simple Orchestrator with 2 tasks
        from src.agent.interfaces import ControlStrategy

        t1 = Task(task_id="a", goal="first",
                  required_capability=CapabilityRequirement(required_tags={"default"}))
        t2 = Task(task_id="b", goal="second",
                  required_capability=CapabilityRequirement(required_tags={"default"}))

        class TwoTaskStrategy(ControlStrategy):
            async def initialize_plan(self, goal, context):
                return ExecutionPlan(tasks={"a": t1, "b": t2})

            async def get_ready_batch(self, results, plan, pending, bus, context):
                return [plan.tasks[tid] for tid in sorted(pending) if tid in plan.tasks]

            async def on_batch_completed(self, results, plan, context):
                return BatchOutcome()

        registry = InMemoryAgentRegistry()
        registry.register_direct("default", make_agent())
        bus = InMemoryMessageBus()
        orch = Orchestrator(TwoTaskStrategy(), registry, bus)

        wrapper = OrchestratorAsAgent(
            orch,
            capability_spec=CapabilitySpec(tags={"orchestrator"}, priority=0),
            aggregator=ResultAggregator(),
        )

        task = Task(task_id="outer", goal="do two things",
                    required_capability=CapabilityRequirement(required_tags={"orchestrator"}))
        result = await wrapper.execute(task, make_context())

        assert result.status == TaskStatus.COMPLETED
        assert result.output is not None

    async def test_sub_task_failure_propagates(self):
        """When sub-tasks fail and allow_partial=False, COMPOSITE returns FAILED."""
        from src.agent.interfaces import ControlStrategy

        t1 = Task(task_id="a", goal="will fail",
                  required_capability=CapabilityRequirement(required_tags={"failer"}))

        class FailStrategy(ControlStrategy):
            async def initialize_plan(self, goal, context):
                return ExecutionPlan(tasks={"a": t1})

            async def get_ready_batch(self, results, plan, pending, bus, context):
                return [plan.tasks[tid] for tid in sorted(pending) if tid in plan.tasks]

            async def on_batch_completed(self, results, plan, context):
                return BatchOutcome()

        fail_agent = make_agent(result_status=TaskStatus.FAILED, tags={"failer"})
        registry = InMemoryAgentRegistry()
        registry.register_direct("failer", fail_agent)
        bus = InMemoryMessageBus()
        orch = Orchestrator(FailStrategy(), registry, bus)

        wrapper = OrchestratorAsAgent(
            orch,
            capability_spec=CapabilitySpec(tags={"orchestrator"}),
            aggregator=ResultAggregator(),
            allow_partial=False,
        )

        task = Task(task_id="outer", goal="will fail",
                    required_capability=CapabilityRequirement(required_tags={"orchestrator"}))
        result = await wrapper.execute(task, make_context())

        assert result.status == TaskStatus.FAILED
        assert result.error is not None
        assert result.error.type == "SubTaskFailed"

    async def test_allow_partial_success(self):
        """With allow_partial=True, partial failures still return COMPLETED."""
        from src.agent.interfaces import ControlStrategy

        t1 = Task(task_id="a", goal="fails",
                  required_capability=CapabilityRequirement(required_tags={"failer"}))
        t2 = Task(task_id="b", goal="succeeds",
                  required_capability=CapabilityRequirement(required_tags={"ok"}))

        class MixedStrategy(ControlStrategy):
            async def initialize_plan(self, goal, context):
                return ExecutionPlan(tasks={"a": t1, "b": t2})

            async def get_ready_batch(self, results, plan, pending, bus, context):
                return [plan.tasks[tid] for tid in sorted(pending) if tid in plan.tasks]

            async def on_batch_completed(self, results, plan, context):
                return BatchOutcome()

        registry = InMemoryAgentRegistry()
        registry.register_direct("failer", make_agent(result_status=TaskStatus.FAILED, tags={"failer"}))
        registry.register_direct("ok", make_agent(output={"ok": True}, tags={"ok"}))
        bus = InMemoryMessageBus()
        orch = Orchestrator(MixedStrategy(), registry, bus)

        wrapper = OrchestratorAsAgent(
            orch,
            capability_spec=CapabilitySpec(tags={"orchestrator"}),
            aggregator=ResultAggregator(),
            allow_partial=True,
        )

        task = Task(task_id="outer", goal="mixed",
                    required_capability=CapabilityRequirement(required_tags={"orchestrator"}))
        result = await wrapper.execute(task, make_context())
        assert result.status == TaskStatus.COMPLETED
