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
from .react import _MODEL_RETRY, _validate
from .tool_execution import ToolExecutionWorkflow, tool_execution_workflow_id


@workflow.defn
class AgentStepWorkflow:
    @workflow.run
    async def run(self, request: AgentStepInput) -> AgentStepResult:
        _validate(request.agent)
        transcript_version = request.transcript_version
        tool_turns = 0
        for turn in range(1, request.max_turns + 1):
            decision = await workflow.execute_activity(
                "model_decision_activity",
                ModelDecisionInput(
                    AGENT_SCHEMA_VERSION,
                    request.agent.run_id,
                    request.agent.account_id,
                    request.agent.conversation_id,
                    request.agent.session_id,
                    request.agent.strategy,
                    request.transcript_id,
                    transcript_version,
                    turn,
                    f"{request.agent.run_id}:plan:{request.plan_version}:step:{request.step.step_id}:turn:{turn}:model",
                    request.agent.lease_token,
                    objective=request.step.objective,
                    plan_id=request.plan_id,
                    plan_version=request.plan_version,
                    step_id=request.step.step_id,
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
                    AGENT_SCHEMA_VERSION,
                    request.agent.run_id,
                    request.agent.account_id,
                    request.agent.conversation_id,
                    request.agent.session_id,
                    request.agent.strategy,
                    request.transcript_id,
                    transcript_version,
                    turn,
                    f"{request.agent.run_id}:plan:{request.plan_version}:step:"
                    f"{request.step.step_id}:turn:{turn}:tool:{call.tool_call_id}",
                    request.agent.lease_token,
                    call,
                    request.plan_id,
                    request.plan_version,
                    request.step.step_id,
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
        final = await workflow.execute_activity(
            "model_decision_activity",
            ModelDecisionInput(
                AGENT_SCHEMA_VERSION,
                request.agent.run_id,
                request.agent.account_id,
                request.agent.conversation_id,
                request.agent.session_id,
                request.agent.strategy,
                request.transcript_id,
                transcript_version,
                final_turn,
                f"{request.agent.run_id}:plan:{request.plan_version}:step:{request.step.step_id}:forced-final",
                request.agent.lease_token,
                objective=request.step.objective,
                final_only=True,
                plan_id=request.plan_id,
                plan_version=request.plan_version,
                step_id=request.step.step_id,
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
