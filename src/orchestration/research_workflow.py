"""Deterministic Research Report Workflow skeleton.

Temporal History contains only the Run id and compact stage references. Plans,
source text, normalized records and evidence remain in PostgreSQL.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import NotRequired, TypedDict

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception
from temporalio.workflow import ActivityCancellationType

from agent_workflows.capability_contracts import CapabilityClose, CapabilityExecutionInput
from agent_workflows.contracts import AGENT_TASK_QUEUE
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE

RESEARCH_WORKFLOW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ResearchWorkflowInput:
    schema_version: int
    run_id: str
    invocation_id: str = ""
    account_id: str = ""
    execution_id: str = ""

    def validate(self) -> None:
        if self.schema_version != RESEARCH_WORKFLOW_SCHEMA_VERSION or not self.run_id:
            raise ApplicationError("invalid Research workflow input", non_retryable=True)


@dataclass(frozen=True)
class ResearchIterationInput:
    schema_version: int
    run_id: str
    iteration: int
    invocation_id: str = ""
    account_id: str = ""
    execution_id: str = ""

    def validate(self) -> None:
        if (
            self.schema_version != RESEARCH_WORKFLOW_SCHEMA_VERSION
            or not self.run_id
            or self.iteration not in (1, 2, 3)
        ):
            raise ApplicationError("invalid Research iteration input", non_retryable=True)


class ResearchStageRef(TypedDict):
    schema_version: int
    run_id: str
    stage: str
    ref: str
    item_count: int
    sufficient: NotRequired[bool]
    cancelled: NotRequired[bool]
    stop_reason: NotRequired[str]
    invocation_id: NotRequired[str]


_RESEARCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=4,
)


@workflow.defn
class ResearchCapabilityWorkflow:
    """R3/R4 fixed stage graph with at most three deterministic iterations."""

    @workflow.run
    async def run(self, child: CapabilityExecutionInput) -> dict[str, str | int]:
        request = ResearchWorkflowInput(
            1, child.run_id, child.invocation_id, child.account_id, child.execution_id
        )
        request.validate()
        try:
            for activity_name, timeout in (
                ("prepare_research_activity", 30),
                ("create_research_plan_activity", 60),
            ):
                await workflow.execute_activity(
                    activity_name,
                    request,
                    task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=timeout),
                    schedule_to_close_timeout=timedelta(seconds=timeout * 2),
                    retry_policy=_RESEARCH_RETRY,
                    heartbeat_timeout=timedelta(seconds=15),
                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                if workflow.cancellation_reason() is not None:
                    raise asyncio.CancelledError
            for iteration in range(1, 4):
                iteration_request = ResearchIterationInput(
                    schema_version=RESEARCH_WORKFLOW_SCHEMA_VERSION,
                    run_id=request.run_id,
                    iteration=iteration,
                    invocation_id=request.invocation_id,
                    account_id=request.account_id,
                    execution_id=request.execution_id,
                )
                for activity_name, timeout in (
                    ("discover_research_sources_activity", 90),
                    ("rank_research_sources_activity", 30),
                    ("fetch_research_sources_activity", 600),
                    ("normalize_research_sources_activity", 60),
                    ("extract_research_evidence_activity", 120),
                    ("assess_research_corroboration_activity", 30),
                ):
                    stage = await workflow.execute_activity(
                        activity_name,
                        iteration_request,
                        task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                        start_to_close_timeout=timedelta(seconds=timeout),
                        schedule_to_close_timeout=timedelta(seconds=timeout * 2),
                        heartbeat_timeout=timedelta(seconds=15),
                        retry_policy=_RESEARCH_RETRY,
                        cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    )
                    if workflow.cancellation_reason() is not None:
                        raise asyncio.CancelledError
                    if stage.get("stop_reason"):
                        break
                if stage.get("stop_reason"):
                    break
                gap = await workflow.execute_activity(
                    "analyze_research_gaps_activity",
                    iteration_request,
                    task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=30),
                    schedule_to_close_timeout=timedelta(seconds=60),
                    retry_policy=_RESEARCH_RETRY,
                    heartbeat_timeout=timedelta(seconds=15),
                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                if workflow.cancellation_reason() is not None:
                    raise asyncio.CancelledError
                if gap.get("sufficient", False):
                    break
            for activity_name, timeout in (
                ("synthesize_research_report_activity", 180),
                ("verify_research_citations_activity", 60),
                ("compare_previous_research_activity", 60),
                ("commit_research_result_activity", 60),
            ):
                await workflow.execute_activity(
                    activity_name,
                    request,
                    task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=timeout),
                    schedule_to_close_timeout=timedelta(seconds=timeout * 2),
                    retry_policy=_RESEARCH_RETRY,
                    heartbeat_timeout=timedelta(seconds=15),
                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                if workflow.cancellation_reason() is not None:
                    raise asyncio.CancelledError
        except ActivityError as exc:
            if is_cancelled_exception(exc) or workflow.cancellation_reason() is not None:
                raise asyncio.CancelledError from exc
            raise
        return {
            "schema_version": RESEARCH_WORKFLOW_SCHEMA_VERSION,
            "run_id": request.run_id,
            "outcome": "result_committed",
        }


@workflow.defn
class ResearchReportWorkflow:
    """Legacy fixed Run entry: admit the same capability, then apply parent delivery policy."""

    @workflow.run
    async def run(self, request: ResearchWorkflowInput) -> dict[str, str | int]:
        request.validate()
        options = dict(
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=60),
            heartbeat_timeout=timedelta(seconds=15),
            retry_policy=_RESEARCH_RETRY,
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        child = await workflow.execute_activity(
            "prepare_research_parent_activity",
            request,
            result_type=CapabilityExecutionInput,
            **options,
        )
        parent = ResearchWorkflowInput(1, request.run_id, child.invocation_id)
        status = "failed"
        try:
            handle = await workflow.start_child_workflow(
                child.workflow_name,
                child,
                id=f"hpagent-capability-{child.execution_id}",
                task_queue=AGENT_TASK_QUEUE,
                cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            await asyncio.wait_for(handle, timeout=child.max_duration_seconds)
            status = "succeeded"
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError) or is_cancelled_exception(exc):
                status = "cancelled"
            raise
        finally:
            await asyncio.shield(
                workflow.execute_activity(
                    "close_capability_activity",
                    CapabilityClose(child, status),
                    task_queue=AGENT_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RESEARCH_RETRY,
                )
            )
        # A deterministic reading view is optional. It cannot invalidate committed Research.
        try:
            await workflow.execute_activity("publish_research_artifact_activity", parent, **options)
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
        try:
            await workflow.execute_activity("save_research_workspace_activity", parent, **options)
            await workflow.execute_activity("complete_research_activity", parent, **options)
        except ActivityError:
            await workflow.execute_activity("fail_research_activity", parent, **options)
            raise
        return {"schema_version": 1, "run_id": request.run_id, "outcome": "report_ready"}
