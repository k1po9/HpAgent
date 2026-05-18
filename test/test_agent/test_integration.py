"""Integration tests — nested orchestration patterns.

TestSupervisorEmbedWorkflow: Supervisor delegates a task to an
  OrchestratorAsAgent wrapping a Workflow DAG.

TestCouncilEmbedWorkflow: Each council voter is an
  OrchestratorAsAgent wrapping a different Workflow DAG.
"""

from src.agent.bus import InMemoryMessageBus
from src.agent.composite import OrchestratorAsAgent
from src.agent.context import ExecutionContext, RuntimeConfig
from src.agent.orchestrator import Orchestrator
from src.agent.registry import InMemoryAgentRegistry
from src.agent.strategies import (
    CouncilControlStrategy,
    MajorityJudge,
    ResultAggregator,
    StubLLMPlanner,
    StubLLMReviewer,
    SupervisorControlStrategy,
    WorkflowControlStrategy,
)
from src.agent.types import (
    CapabilityRequirement,
    CapabilitySpec,
    Task,
    TaskResult,
    TaskStatus,
)


def make_agent(tags=None):
    from src.agent.interfaces import BaseAgent

    class Stub(BaseAgent):
        @property
        def capability(self) -> CapabilitySpec:
            return CapabilitySpec(tags=set(tags or ["default"]))

        async def execute(self, task, context):
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                output={"done": True},
            )

    return Stub()


def make_context(timeout=5):
    return ExecutionContext(config=RuntimeConfig(timeout_seconds=timeout))


class TestSupervisorEmbedWorkflow:
    """Supervisor delegates tasks to OrchestratorAsAgent wrapping Workflow."""

    async def test_supervisor_delegates_to_workflow_agent(self):
        """Supervisor plan includes a task for a Workflow-wrapped agent."""
        # --- Inner Workflow: 2-task sequential DAG ---
        wf_a = Task(
            task_id="wf_a",
            goal="workflow step A",
            required_capability=CapabilityRequirement(required_tags={"default"}),
        )
        wf_b = Task(
            task_id="wf_b",
            goal="workflow step B",
            required_capability=CapabilityRequirement(required_tags={"default"}),
        )
        inner_workflow = WorkflowControlStrategy(
            dag_tasks={"wf_a": wf_a, "wf_b": wf_b},
            dag_dependencies={"wf_b": ["wf_a"]},
        )

        inner_registry = InMemoryAgentRegistry()
        inner_registry.register_direct("default", make_agent())
        inner_bus = InMemoryMessageBus()
        inner_orch = Orchestrator(inner_workflow, inner_registry, inner_bus)

        workflow_agent = OrchestratorAsAgent(
            inner_orch,
            capability_spec=CapabilitySpec(tags={"workflow"}, priority=5),
            aggregator=ResultAggregator(),
        )

        # --- Outer Supervisor ---
        direct_task = Task(
            task_id="direct",
            goal="direct task",
            required_capability=CapabilityRequirement(required_tags={"default"}),
        )
        nested_task = Task(
            task_id="nested",
            goal="do workflow",
            required_capability=CapabilityRequirement(required_tags={"workflow"}),
        )
        planner = StubLLMPlanner({"test": ([direct_task, nested_task], {})})
        reviewer = StubLLMReviewer([(True, None)])

        outer_strategy = SupervisorControlStrategy(planner, reviewer)
        outer_registry = InMemoryAgentRegistry()
        outer_registry.register_direct("default", make_agent())
        outer_registry.register_direct("workflow", workflow_agent)
        outer_bus = InMemoryMessageBus()
        outer_orch = Orchestrator(outer_strategy, outer_registry, outer_bus)

        results = await outer_orch.run("test", make_context())

        # Both outer tasks completed
        assert "direct" in results
        assert results["direct"].status == TaskStatus.COMPLETED
        assert "nested" in results
        assert results["nested"].status == TaskStatus.COMPLETED
        # The nested result output contains the merged inner workflow results
        # merge strategy overwrites overlapping keys; last task's output dominates
        assert results["nested"].output is not None
        assert results["nested"].output.get("done") is True


class FirstResultAggregator(ResultAggregator):
    """Aggregator that always uses 'first' strategy, for simple inner agents."""
    async def aggregate(self, results, strategy="concat", context=None):
        return await super().aggregate(results, strategy="first", context=context)


class TestCouncilEmbedWorkflow:
    """Each council voter is an OrchestratorAsAgent wrapping a Workflow."""

    async def test_council_with_workflow_voters(self):
        """Two workflow agents vote; judge picks majority."""
        def make_workflow_agent(tags, output_value):
            """Create an OrchestratorAsAgent wrapping a single-task workflow."""
            from src.agent.interfaces import BaseAgent

            class OutputAgent(BaseAgent):
                @property
                def capability(self) -> CapabilitySpec:
                    return CapabilitySpec(tags={"inner"})

                async def execute(self, task, context):
                    return TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.COMPLETED,
                        output=output_value,
                    )

            inner_task = Task(
                task_id="step",
                goal="produce output",
                required_capability=CapabilityRequirement(required_tags={"inner"}),
            )
            inner_strategy = WorkflowControlStrategy(
                dag_tasks={"step": inner_task},
            )
            inner_registry = InMemoryAgentRegistry()
            inner_registry.register_direct("inner", OutputAgent())
            inner_bus = InMemoryMessageBus()
            inner_orch = Orchestrator(inner_strategy, inner_registry, inner_bus)

            return OrchestratorAsAgent(
                inner_orch,
                capability_spec=CapabilitySpec(tags=tags, priority=5),
                aggregator=FirstResultAggregator(),
            )

        agent_a = make_workflow_agent({"voter_a"}, "blue")
        agent_b = make_workflow_agent({"voter_b"}, "blue")
        agent_c = make_workflow_agent({"voter_c"}, "red")

        caps = [
            CapabilityRequirement(required_tags={"voter_a"}),
            CapabilityRequirement(required_tags={"voter_b"}),
            CapabilityRequirement(required_tags={"voter_c"}),
        ]
        judge = MajorityJudge()
        strategy = CouncilControlStrategy(caps, judge, council_name="test_council")

        registry = InMemoryAgentRegistry()
        registry.register_direct("voter_a", agent_a)
        registry.register_direct("voter_b", agent_b)
        registry.register_direct("voter_c", agent_c)
        bus = InMemoryMessageBus()
        orch = Orchestrator(strategy, registry, bus)

        results = await orch.run("pick_color", make_context())
        assert "test_council_verdict" in results
        verdict = results["test_council_verdict"]
        assert verdict.status == TaskStatus.COMPLETED
        assert verdict.output["verdict"] == "blue"
        assert verdict.output["votes"] == 2
