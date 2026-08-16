"""Stable Agent strategy router workflow."""
from __future__ import annotations

from typing import Any, cast

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from .contracts import (
    AGENT_STRATEGY_PLAN,
    AGENT_STRATEGY_REACT,
    AGENT_TASK_QUEUE,
    AgentResult,
    AgentRunInput,
)
from .plan_execute import PlanAndExecuteWorkflow
from .react import ReactAgentWorkflow, _validate


@workflow.defn
class AgentRunWorkflow:
    @workflow.run
    async def run(self, request: AgentRunInput) -> AgentResult:
        _validate(request)
        workflow.logger.info(
            "agent_workflow_started",
            extra={"event": "agent_workflow_started", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "status": "started"},
        )
        target: Any
        if request.strategy == AGENT_STRATEGY_REACT:
            target = ReactAgentWorkflow.run
        elif request.strategy == AGENT_STRATEGY_PLAN:
            target = PlanAndExecuteWorkflow.run
        else:
            raise ApplicationError("unsupported agent strategy", type="unsupported_agent_strategy", non_retryable=True)
        workflow.logger.info(
            "agent_strategy_selected",
            extra={"event": "agent_strategy_selected", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "status": "selected"},
        )
        result = await workflow.execute_child_workflow(
            target,
            request,
            id=f"hpagent-agent-{request.strategy}-{request.run_id}",
            task_queue=AGENT_TASK_QUEUE,
        )
        workflow.logger.info(
            "agent_workflow_completed",
            extra={"event": "agent_workflow_completed", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "status": "success", "result_ref": result.result_ref},
        )
        return cast(AgentResult, result)
