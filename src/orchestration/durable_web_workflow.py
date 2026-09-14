"""Web business lifecycle using durable child Agent workflows.

The Web adapter builds source/context and active-interval contracts from PG.
Agent Activities own individual segments; no lease spans the child Workflow.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    is_cancelled_exception,
)

from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    AgentRunInput,
    FinalizeResultInput,
)

from .web_workflow import (
    _FINALIZE_RETRY,
    _LIFECYCLE_RETRY,
    _STABLE_FAILURE_MESSAGES,
    WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_FINALIZE_START_TO_CLOSE_SECONDS,
    WEB_LIFECYCLE_TASK_QUEUE,
    WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_PREPARE_START_TO_CLOSE_SECONDS,
    FailureInput,
    RunAuthority,
    WebRunWorkflowInput,
)

_DURABLE_FAILURE_MESSAGES = {
    **_STABLE_FAILURE_MESSAGES,
    "execution_lease_conflict": "该账号已有任务正在执行。",
    "stale_fencing_token": "执行租约已失效。",
    "transcript_version_conflict": "Agent 状态版本冲突。",
    "unsupported_agent_strategy": "不支持的 Agent 策略。",
    "planning_failed": "计划生成失败。",
    "plan_evaluation_failed": "计划评估失败。",
    "plan_replan_limit_reached": "计划重规划次数过多。",
    "tool_side_effect_uncertain": "工具可能已执行，无法安全确认结果。",
    "side_effect_reconciliation_failed": "工具副作用恢复失败。",
}


def _completed(run_id: str) -> dict[str, str | int]:
    return {"schema_version": 1, "run_id": run_id, "outcome": "completed"}


def _failure_type(error: BaseException) -> str:
    current: BaseException | None = error
    while current is not None:
        value = getattr(current, "type", "")
        if value:
            return str(value)
        current = getattr(current, "cause", None)
    return ""


@workflow.defn
class DurableWebRunWorkflow:
    @workflow.run
    async def run(self, request: WebRunWorkflowInput) -> dict[str, str | int]:
        request.validate()
        workflow.logger.info("durable_web_workflow_started", extra={"event": "durable_web_workflow_started", "component": "workflow", "run_id": request.run_id, "workflow_type": "DurableWebRunWorkflow", "workflow_id": workflow.info().workflow_id, "workflow_run_id": workflow.info().run_id, "status": "started", "phase": "prepare"})
        try:
            prepared: RunAuthority = await workflow.execute_activity(
                "prepare_run_activity",
                request,
                task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS),
                start_to_close_timeout=timedelta(seconds=WEB_PREPARE_START_TO_CLOSE_SECONDS),
                retry_policy=_LIFECYCLE_RETRY,
            )
            status = prepared["status"]
            workflow.logger.info("durable_web_workflow_prepared", extra={"event": "durable_web_workflow_prepared", "component": "workflow", "run_id": request.run_id, "workflow_type": "DurableWebRunWorkflow", "status": status, "phase": "prepare"})
            if status == "completed":
                return _completed(request.run_id)
            if status in ("cancelling", "cancelled"):
                raise asyncio.CancelledError
            if status == "failed":
                raise ApplicationError("run already failed", non_retryable=True)

            identity = await workflow.execute_activity(
                "load_agent_run_input_activity", request,
                task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                result_type=AgentRunInput,
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=_LIFECYCLE_RETRY,
            )
            agent_result = await workflow.execute_child_workflow(
                AgentRunWorkflow.run,
                identity,
                id=f"hpagent-agent-run-{request.run_id}",
                task_queue=AGENT_TASK_QUEUE,
            )
            workflow.logger.info("durable_web_workflow_agent_completed", extra={"event": "durable_web_workflow_agent_completed", "component": "workflow", "run_id": request.run_id, "strategy": identity.strategy, "status": "success", "phase": "agent"})
            workflow.logger.info("durable_web_workflow_finalizing", extra={"event": "durable_web_workflow_finalizing", "component": "workflow", "run_id": request.run_id, "strategy": identity.strategy, "status": "started", "phase": "finalize"})
            authority: RunAuthority = await workflow.execute_activity(
                "finalize_agent_result_activity",
                FinalizeResultInput(AGENT_SCHEMA_VERSION, request.run_id, agent_result.result_ref),
                task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS),
                start_to_close_timeout=timedelta(seconds=WEB_FINALIZE_START_TO_CLOSE_SECONDS),
                retry_policy=_FINALIZE_RETRY,
            )
            if authority["status"] != "completed":
                raise ApplicationError("Agent finalization returned non-completed status", non_retryable=True)
            workflow.logger.info("durable_web_workflow_completed", extra={"event": "durable_web_workflow_completed", "component": "workflow", "run_id": request.run_id, "strategy": identity.strategy, "status": "success", "phase": "completed"})
            return _completed(request.run_id)
        except asyncio.CancelledError:
            workflow.logger.warning("durable_web_workflow_cancelled", extra={"event": "durable_web_workflow_cancelled", "component": "workflow", "run_id": request.run_id, "status": "cancelled", "phase": "finalize"})
            authority = await workflow.execute_activity(
                    "finalize_cancelled_activity",
                    request,
                    task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                    schedule_to_close_timeout=timedelta(seconds=WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS),
                    start_to_close_timeout=timedelta(seconds=WEB_FINALIZE_START_TO_CLOSE_SECONDS),
                    retry_policy=_FINALIZE_RETRY,
                )
            if authority["status"] == "completed":
                    return _completed(request.run_id)
            if authority["status"] == "failed":
                raise ApplicationError("unexpected workflow cancellation", non_retryable=True)
            raise
        except (ActivityError, ChildWorkflowError) as exc:
            if is_cancelled_exception(exc) or _failure_type(exc) == "run_not_executable":
                authority = await workflow.execute_activity(
                    "finalize_cancelled_activity",
                    request,
                    task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                    schedule_to_close_timeout=timedelta(seconds=WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS),
                    start_to_close_timeout=timedelta(seconds=WEB_FINALIZE_START_TO_CLOSE_SECONDS),
                    retry_policy=_FINALIZE_RETRY,
                )
                if authority["status"] == "completed":
                    return _completed(request.run_id)
                if authority["status"] == "failed":
                    raise ApplicationError("unexpected workflow cancellation", non_retryable=True) from exc
                raise asyncio.CancelledError from exc
            failure_type = _failure_type(exc)
            workflow.logger.error("durable_web_workflow_failed", extra={"event": "durable_web_workflow_failed", "component": "workflow", "run_id": request.run_id, "status": "failed", "phase": "finalize", "error_code": failure_type or "internal_execution_error"})
            error_code = failure_type if failure_type in _DURABLE_FAILURE_MESSAGES else "internal_execution_error"
            authority = await workflow.execute_activity(
                "finalize_failed_activity",
                FailureInput(1, request.run_id, error_code, _DURABLE_FAILURE_MESSAGES.get(error_code, "执行未能完成。")),
                task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS),
                start_to_close_timeout=timedelta(seconds=WEB_FINALIZE_START_TO_CLOSE_SECONDS),
                retry_policy=_FINALIZE_RETRY,
            )
            if authority["status"] == "completed":
                    return _completed(request.run_id)
            raise
