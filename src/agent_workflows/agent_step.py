"""Durable tool loop scoped to one plan step."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from .contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    AgentStepInput,
    AgentStepResult,
    ModelDecisionInput,
    ModelDecisionResult,
    ToolExecutionInput,
    ToolExecutionResult,
)
from .ids import tool_execution_workflow_id
from .react import _MODEL_RETRY, _validate
from .segments import execute_segment
from .tool_execution import ToolExecutionWorkflow


@workflow.defn
class AgentStepWorkflow:
    @workflow.run
    async def run(self, request: AgentStepInput) -> AgentStepResult:
        _validate(request.agent)
        transcript_version = request.transcript_version
        tool_turns = 0
        for turn in range(1, request.max_turns + 1):
            decision = await execute_segment(
                "model_decision_activity",
                ModelDecisionInput(
                    schema_version=AGENT_SCHEMA_VERSION,
                    run_id=request.agent.run_id,
                    account_id=request.agent.account_id,
                    strategy=request.agent.strategy,
                    transcript_id=request.transcript_id,
                    transcript_version=transcript_version,
                    turn=turn,
                    operation_id=f"{request.agent.run_id}:plan:{request.plan_version}:step:{request.step.step_id}:turn:{turn}:model",
                    lease_token=0,
                    objective=request.step.objective,
                    plan_id=request.plan_id,
                    plan_version=request.plan_version,
                    step_id=request.step.step_id,
                    source=request.agent.source,
                    context=request.agent.context,
                ),
                task_queue=AGENT_TASK_QUEUE,
                result_type=ModelDecisionResult,
                start_to_close_timeout=timedelta(seconds=300),
                retry_policy=_MODEL_RETRY,
            )
            transcript_version = decision.transcript_version
            if decision.decision_type == "final":
                return AgentStepResult(
                    AGENT_SCHEMA_VERSION,
                    request.step.step_id,
                    decision.decision_ref,
                    transcript_version,
                    tool_turns,
                )
            for call in decision.tool_calls:
                tool_input = ToolExecutionInput(
                    schema_version=AGENT_SCHEMA_VERSION,
                    run_id=request.agent.run_id,
                    account_id=request.agent.account_id,
                    strategy=request.agent.strategy,
                    transcript_id=request.transcript_id,
                    transcript_version=transcript_version,
                    turn=turn,
                    operation_id=f"{request.agent.run_id}:plan:{request.plan_version}:step:"
                    f"{request.step.step_id}:turn:{turn}:tool:{call.tool_call_id}",
                    lease_token=0,
                    tool_call=call,
                    plan_id=request.plan_id,
                    plan_version=request.plan_version,
                    step_id=request.step.step_id,
                    source=request.agent.source,
                    context=request.agent.context,
                )
                result = await workflow.execute_child_workflow(
                    ToolExecutionWorkflow.run,
                    tool_input,
                    id=tool_execution_workflow_id(
                        request.agent.run_id, tool_input.operation_id
                    ),
                    task_queue=AGENT_TASK_QUEUE,
                    result_type=ToolExecutionResult,
                )
                transcript_version = result.transcript_version
            tool_turns += 1
        final_turn = request.max_turns + 1
        final = await execute_segment(
            "model_decision_activity",
            ModelDecisionInput(
                schema_version=AGENT_SCHEMA_VERSION,
                run_id=request.agent.run_id,
                account_id=request.agent.account_id,
                strategy=request.agent.strategy,
                transcript_id=request.transcript_id,
                transcript_version=transcript_version,
                turn=final_turn,
                operation_id=f"{request.agent.run_id}:plan:{request.plan_version}:step:{request.step.step_id}:forced-final",
                lease_token=0,
                objective=request.step.objective,
                final_only=True,
                plan_id=request.plan_id,
                plan_version=request.plan_version,
                step_id=request.step.step_id,
                source=request.agent.source,
                context=request.agent.context,
            ),
            task_queue=AGENT_TASK_QUEUE,
            result_type=ModelDecisionResult,
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=_MODEL_RETRY,
        )
        return AgentStepResult(
            AGENT_SCHEMA_VERSION,
            request.step.step_id,
            final.decision_ref,
            final.transcript_version,
            tool_turns,
        )
