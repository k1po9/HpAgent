"""Durable Plan-and-Execute parent workflow."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from .agent_step import AgentStepWorkflow
from .contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    AgentExecutionInput,
    AgentResult,
    AgentStepInput,
    ModelDecisionInput,
    ModelDecisionResult,
    PlanEvaluationInput,
    PlanEvaluationResult,
    PlanningInput,
    PlanningResult,
)
from .react import _MODEL_RETRY, _validate, bootstrap


@workflow.defn
class PlanAndExecuteWorkflow:
    @workflow.run
    async def run(self, request: AgentExecutionInput) -> AgentResult:
        _validate(request)
        plan_id = f"plan:{request.run_id}"
        plan_version = 1
        workflow.logger.info(
            "plan_workflow_started",
            extra={"event": "plan_workflow_started", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "plan_id": plan_id, "plan_version": plan_version, "status": "started"},
        )
        context = await bootstrap(request)
        plan = await workflow.execute_activity(
            "planning_activity",
            PlanningInput(
                schema_version=AGENT_SCHEMA_VERSION,
                run_id=request.run_id,
                account_id=request.account_id,
                transcript_id=context.transcript_id,
                transcript_version=context.transcript_version,
                operation_id=f"{request.run_id}:plan:{plan_version}:planner",
                lease_token=request.execution_lease.fencing_token,
                plan_id=plan_id,
                plan_version=plan_version,
                source=request.source,
                context=request.context,
            ),
            task_queue=AGENT_TASK_QUEUE,
            result_type=PlanningResult,
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=_MODEL_RETRY,
        )
        transcript_version = plan.transcript_version
        tool_turns = 0
        replan_count = 0
        completed_step_refs: list[str] = []
        while True:
            replan_requested = False
            for index, step in enumerate(plan.steps, 1):
                workflow.logger.info(
                    "plan_step_started",
                    extra={"event": "plan_step_started", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "plan_id": plan_id, "plan_version": plan_version, "step_id": step.step_id, "step_index": index, "step_count": len(plan.steps), "status": "started"},
                )
                result = await workflow.execute_child_workflow(
                    AgentStepWorkflow.run,
                    AgentStepInput(
                        AGENT_SCHEMA_VERSION,
                        request,
                        context.transcript_id,
                        transcript_version,
                        plan_id,
                        plan_version,
                        step,
                    ),
                    id=f"hpagent-agent-step-{request.run_id}-{plan_version}-{step.step_id}",
                    task_queue=AGENT_TASK_QUEUE,
                )
                transcript_version = result.transcript_version
                tool_turns += result.tool_turns
                completed_step_refs.append(result.result_ref)
                workflow.logger.info(
                    "plan_step_completed",
                    extra={"event": "plan_step_completed", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "plan_id": plan_id, "plan_version": plan_version, "step_id": step.step_id, "step_index": index, "step_count": len(plan.steps), "status": "success", "result_ref": result.result_ref},
                )
                evaluation = await workflow.execute_activity(
                    "evaluate_plan_activity",
                    PlanEvaluationInput(
                        schema_version=AGENT_SCHEMA_VERSION,
                        run_id=request.run_id,
                        account_id=request.account_id,
                        transcript_id=context.transcript_id,
                        transcript_version=transcript_version,
                        operation_id=f"{request.run_id}:plan:{plan_version}:step:{step.step_id}:evaluation",
                        lease_token=request.execution_lease.fencing_token,
                        plan_id=plan_id,
                        plan_version=plan_version,
                        step_id=step.step_id,
                        step_index=index,
                        step_count=len(plan.steps),
                        source=request.source,
                        context=request.context,
                    ),
                    task_queue=AGENT_TASK_QUEUE,
                    result_type=PlanEvaluationResult,
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=_MODEL_RETRY,
                )
                if evaluation.decision == "fail":
                    raise ApplicationError(
                        "plan evaluation failed",
                        type="plan_evaluation_failed",
                        non_retryable=True,
                    )
                if evaluation.decision == "replan":
                    replan_count += 1
                    if replan_count > 3:
                        raise ApplicationError(
                            "plan replan limit reached",
                            type="plan_replan_limit_reached",
                            non_retryable=True,
                        )
                    plan_version += 1
                    workflow.logger.info(
                        "replan_started",
                        extra={"event": "replan_started", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "plan_id": plan_id, "plan_version": plan_version, "replan_count": replan_count, "status": "started"},
                    )
                    plan = await workflow.execute_activity(
                        "planning_activity",
                        PlanningInput(
                            schema_version=AGENT_SCHEMA_VERSION,
                            run_id=request.run_id,
                            account_id=request.account_id,
                            transcript_id=context.transcript_id,
                            transcript_version=transcript_version,
                            operation_id=f"{request.run_id}:plan:{plan_version}:planner",
                            lease_token=request.execution_lease.fencing_token,
                            plan_id=plan_id,
                            plan_version=plan_version,
                            previous_plan_ref=f"agent-plan:{plan_id}:v{plan_version - 1}",
                            previous_plan_version=plan_version - 1,
                            completed_step_refs=tuple(completed_step_refs),
                            trigger_step_id=step.step_id,
                            evaluation_reason=evaluation.reason,
                            source=request.source,
                            context=request.context,
                        ),
                        task_queue=AGENT_TASK_QUEUE,
                        result_type=PlanningResult,
                        start_to_close_timeout=timedelta(seconds=300),
                        retry_policy=_MODEL_RETRY,
                    )
                    transcript_version = plan.transcript_version
                    replan_requested = True
                    workflow.logger.info(
                        "replan_completed",
                        extra={"event": "replan_completed", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "plan_id": plan_id, "plan_version": plan_version, "replan_count": replan_count, "status": "success"},
                    )
                    break
                if evaluation.decision == "complete":
                    break
            if replan_requested:
                continue
            break
        workflow.logger.info(
            "plan_synthesis_started",
            extra={"event": "plan_synthesis_started", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "plan_id": plan_id, "plan_version": plan_version, "status": "started"},
        )
        final = await workflow.execute_activity(
            "model_decision_activity",
            ModelDecisionInput(
                schema_version=AGENT_SCHEMA_VERSION,
                run_id=request.run_id,
                account_id=request.account_id,
                strategy=request.strategy,
                transcript_id=context.transcript_id,
                transcript_version=transcript_version,
                turn=len(plan.steps) + 1,
                operation_id=f"{request.run_id}:plan:{plan_version}:synthesis",
                lease_token=request.execution_lease.fencing_token,
                objective="综合所有已完成步骤，直接回答用户最初的问题。",
                final_only=True,
                plan_id=plan_id,
                plan_version=plan_version,
                source=request.source,
                context=request.context,
            ),
            task_queue=AGENT_TASK_QUEUE,
            result_type=ModelDecisionResult,
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=_MODEL_RETRY,
        )
        workflow.logger.info(
            "plan_workflow_completed",
            extra={"event": "plan_workflow_completed", "component": "workflow", "run_id": request.run_id, "strategy": request.strategy, "plan_id": plan_id, "plan_version": plan_version, "status": "success"},
        )
        return AgentResult(
            AGENT_SCHEMA_VERSION,
            request.run_id,
            final.decision_ref,
            context.transcript_id,
            final.transcript_version,
            tool_turns,
        )
