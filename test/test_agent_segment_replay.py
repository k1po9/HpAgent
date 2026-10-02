"""Offline replay of current Execution contracts and bounded delegation histories."""

from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.agent_step import AgentStepWorkflow
from agent_workflows.delegation import WorkDelegationWorkflow
from agent_workflows.plan_execute import PlanAndExecuteWorkflow
from agent_workflows.react import ReactAgentWorkflow
from agent_workflows.tool_execution import ToolExecutionWorkflow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workflow_type,filename",
    [
        (AgentRunWorkflow, "root.json"),
        (AgentRunWorkflow, "root_plan.json"),
        (ReactAgentWorkflow, "react.json"),
        (ReactAgentWorkflow, "child.json"),
        (PlanAndExecuteWorkflow, "plan.json"),
        (AgentStepWorkflow, "step.json"),
        (ToolExecutionWorkflow, "tool.json"),
        (WorkDelegationWorkflow, "delegation.json"),
    ],
)
async def test_current_execution_histories_replay_offline(workflow_type, filename):
    fixture = Path(__file__).parent / "fixtures" / "phase5" / filename
    history = WorkflowHistory.from_json("phase5-replay", fixture.read_text())
    replay = await Replayer(workflows=[workflow_type]).replay_workflow(history)
    assert replay.replay_failure is None
