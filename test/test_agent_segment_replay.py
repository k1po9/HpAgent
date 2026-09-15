"""Offline target History replay; these are W1-B fixtures, not legacy History."""
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from agent_workflows.plan_execute import PlanAndExecuteWorkflow
from agent_workflows.react import ReactAgentWorkflow
from agent_workflows.tool_execution import ToolExecutionWorkflow
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow


@pytest.mark.asyncio
@pytest.mark.parametrize('workflow_type,filename', [
    (AgentLifecycleWorkflow, 'lifecycle_react_completed.json'),
    (AgentLifecycleWorkflow, 'lifecycle_plan_and_execute_completed.json'),
    (ReactAgentWorkflow, 'react_segments_completed.json'),
    (PlanAndExecuteWorkflow, 'plan_segments_completed.json'),
    (ToolExecutionWorkflow, 'tool_approval_segments_completed.json'),
])
async def test_w1b_completed_segment_histories_replay_offline(workflow_type, filename):
    fixture = Path(__file__).parent / 'fixtures' / 'phase3' / filename
    history = WorkflowHistory.from_json('w1b-replay', fixture.read_text())
    replay = await Replayer(workflows=[workflow_type]).replay_workflow(history)
    assert replay.replay_failure is None
