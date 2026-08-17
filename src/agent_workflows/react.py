"""Durable ReAct state machine: compact state in History, payloads by ref."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from .contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    AgentResult,
    AgentRunInput,
    ContextBootstrapInput,
    ContextBootstrapResult,
    ModelDecisionInput,
    ModelDecisionResult,
    ToolExecutionInput,
    ToolExecutionResult,
)

_READ_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)
_MODEL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=3,
)
_TOOL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)


def _validate(request: AgentRunInput) -> None:
    if request.schema_version != AGENT_SCHEMA_VERSION:
        raise ApplicationError("unsupported Agent workflow schema", non_retryable=True)
    if not all((request.run_id, request.account_id, request.conversation_id, request.session_id)):
        raise ApplicationError("incomplete Agent identity", non_retryable=True)
    if request.lease_token < 1 or request.max_turns < 1:
        raise ApplicationError("invalid Agent execution limits", non_retryable=True)


async def bootstrap(request: AgentRunInput):
    return await workflow.execute_activity(
        "context_bootstrap_activity",
        ContextBootstrapInput(
            AGENT_SCHEMA_VERSION,
            request.run_id,
            request.account_id,
            request.conversation_id,
            request.session_id,
            request.strategy,
            f"{request.run_id}:{request.strategy}:context",
        ),
        task_queue=AGENT_TASK_QUEUE,
        result_type=ContextBootstrapResult,
        start_to_close_timeout=timedelta(seconds=120),
        retry_policy=_READ_RETRY,
    )


@workflow.defn
class ReactAgentWorkflow:
    @workflow.run
    async def run(self, request: AgentRunInput) -> AgentResult:
        _validate(request)
        workflow.logger.info(
            "react_workflow_started",
            extra={"event": "react_workflow_started", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "status": "started"},
        )
        context = await bootstrap(request)
        transcript_version = context.transcript_version
        tool_turns = 0
        for turn in range(1, request.max_turns + 1):
            workflow.logger.info(
                "react_turn_started",
                extra={"event": "react_turn_started", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "turn": turn, "status": "started"},
            )
            decision = await workflow.execute_activity(
                "model_decision_activity",
                ModelDecisionInput(
                    AGENT_SCHEMA_VERSION,
                    request.run_id,
                    request.account_id,
                    request.conversation_id,
                    request.session_id,
                    request.strategy,
                    context.transcript_id,
                    transcript_version,
                    turn,
                    f"{request.run_id}:react:turn:{turn}:model",
                    request.lease_token,
                ),
                task_queue=AGENT_TASK_QUEUE,
                result_type=ModelDecisionResult,
                start_to_close_timeout=timedelta(seconds=300),
                retry_policy=_MODEL_RETRY,
            )
            transcript_version = decision.transcript_version
            workflow.logger.info(
                "react_turn_decision",
                extra={"event": "react_turn_decision", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "turn": turn, "decision_type": decision.decision_type, "tool_count": len(decision.tool_calls)},
            )
            if decision.decision_type == "final":
                workflow.logger.info(
                    "react_workflow_completed",
                    extra={"event": "react_workflow_completed", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "turn": turn, "status": "success"},
                )
                return AgentResult(
                    AGENT_SCHEMA_VERSION,
                    request.run_id,
                    decision.decision_ref,
                    context.transcript_id,
                    transcript_version,
                    tool_turns,
                )
            for call in decision.tool_calls:
                tool_result = await workflow.execute_activity(
                    "tool_execution_activity",
                    ToolExecutionInput(
                        AGENT_SCHEMA_VERSION,
                        request.run_id,
                        request.account_id,
                        request.conversation_id,
                        request.session_id,
                        request.strategy,
                        context.transcript_id,
                        transcript_version,
                        turn,
                        f"{request.run_id}:react:turn:{turn}:tool:{call.tool_call_id}",
                        request.lease_token,
                        call,
                    ),
                    task_queue=AGENT_TASK_QUEUE,
                    result_type=ToolExecutionResult,
                    start_to_close_timeout=timedelta(seconds=600),
                    heartbeat_timeout=timedelta(seconds=45),
                    retry_policy=_TOOL_RETRY,
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                transcript_version = tool_result.transcript_version
            tool_turns += 1
            workflow.logger.info(
                "react_turn_completed",
                extra={"event": "react_turn_completed", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "turn": turn, "status": "success"},
            )

        workflow.logger.warning(
            "react_max_turns_reached",
            extra={"event": "react_max_turns_reached", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "turn": request.max_turns},
        )
        final_turn = request.max_turns + 1
        final = await workflow.execute_activity(
            "model_decision_activity",
            ModelDecisionInput(
                AGENT_SCHEMA_VERSION,
                request.run_id,
                request.account_id,
                request.conversation_id,
                request.session_id,
                request.strategy,
                context.transcript_id,
                transcript_version,
                final_turn,
                f"{request.run_id}:react:turn:{final_turn}:forced-final",
                request.lease_token,
                final_only=True,
            ),
            task_queue=AGENT_TASK_QUEUE,
            result_type=ModelDecisionResult,
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=_MODEL_RETRY,
        )
        return AgentResult(
            AGENT_SCHEMA_VERSION,
            request.run_id,
            final.decision_ref,
            context.transcript_id,
            final.transcript_version,
            tool_turns,
        )
