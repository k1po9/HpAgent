"""Deterministic Research Report Workflow skeleton.

Temporal History contains only the Run id and compact stage references. Plans,
source text, normalized records and evidence remain in PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import NotRequired, TypedDict, cast

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from orchestration.web_workflow import WEB_LIFECYCLE_TASK_QUEUE

RESEARCH_WORKFLOW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ResearchWorkflowInput:
    schema_version: int
    run_id: str

    def validate(self) -> None:
        if self.schema_version != RESEARCH_WORKFLOW_SCHEMA_VERSION or not self.run_id:
            raise ApplicationError("invalid Research workflow input", non_retryable=True)


@dataclass(frozen=True)
class ResearchIterationInput:
    schema_version: int
    run_id: str
    iteration: int

    def validate(self) -> None:
        if (
            self.schema_version != RESEARCH_WORKFLOW_SCHEMA_VERSION
            or not self.run_id
            or self.iteration not in (1, 2, 3)
        ):
            raise ApplicationError("invalid Research iteration input", non_retryable=True)


@dataclass(frozen=True)
class ResearchScheduleInput:
    schema_version: int
    account_id: str
    task_id: str
    schedule_version: int

    def validate(self) -> None:
        if (
            self.schema_version != RESEARCH_WORKFLOW_SCHEMA_VERSION
            or not self.account_id
            or not self.task_id
            or self.schedule_version < 1
        ):
            raise ApplicationError("invalid Research schedule input", non_retryable=True)


class ResearchStageRef(TypedDict):
    schema_version: int
    run_id: str
    stage: str
    ref: str
    item_count: int
    sufficient: NotRequired[bool]


_RESEARCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=4,
)


@workflow.defn
class ResearchTaskScheduleWorkflow:
    """Temporal Schedule entrypoint; the Activity still invokes the Task command path."""

    @workflow.run
    async def run(self, request: ResearchScheduleInput) -> dict[str, str | bool]:
        request.validate()
        result = await workflow.execute_activity(
            "trigger_scheduled_research_activity",
            {
                "schema_version": request.schema_version,
                "account_id": request.account_id,
                "task_id": request.task_id,
                "schedule_version": request.schedule_version,
                "fire_id": workflow.info().run_id,
            },
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            schedule_to_close_timeout=timedelta(seconds=60),
            retry_policy=_RESEARCH_RETRY,
        )
        return cast(dict[str, str | bool], result)


@workflow.defn
class ResearchReportWorkflow:
    """R3/R4 fixed stage graph with at most three deterministic iterations."""

    @workflow.run
    async def run(self, request: ResearchWorkflowInput) -> dict[str, str | int]:
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
                )
            for iteration in range(1, 4):
                iteration_request = ResearchIterationInput(
                    schema_version=RESEARCH_WORKFLOW_SCHEMA_VERSION,
                    run_id=request.run_id,
                    iteration=iteration,
                )
                for activity_name, timeout in (
                    ("discover_research_sources_activity", 90),
                    ("rank_research_sources_activity", 30),
                    ("fetch_research_sources_activity", 600),
                    ("normalize_research_sources_activity", 60),
                    ("extract_research_evidence_activity", 120),
                    ("assess_research_corroboration_activity", 30),
                ):
                    await workflow.execute_activity(
                        activity_name,
                        iteration_request,
                        task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                        start_to_close_timeout=timedelta(seconds=timeout),
                        schedule_to_close_timeout=timedelta(seconds=timeout * 2),
                        heartbeat_timeout=(
                            timedelta(seconds=15)
                            if activity_name == "fetch_research_sources_activity"
                            else None
                        ),
                        retry_policy=_RESEARCH_RETRY,
                    )
                gap = await workflow.execute_activity(
                    "analyze_research_gaps_activity",
                    iteration_request,
                    task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=30),
                    schedule_to_close_timeout=timedelta(seconds=60),
                    retry_policy=_RESEARCH_RETRY,
                )
                if gap.get("sufficient", False):
                    break
            for activity_name, timeout in (
                ("synthesize_research_report_activity", 180),
                ("verify_research_citations_activity", 60),
                ("compare_previous_research_activity", 60),
                ("publish_research_artifact_activity", 60),
                ("complete_research_activity", 30),
            ):
                await workflow.execute_activity(
                    activity_name,
                    request,
                    task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=timeout),
                    schedule_to_close_timeout=timedelta(seconds=timeout * 2),
                    retry_policy=_RESEARCH_RETRY,
                )
        except ActivityError:
            await workflow.execute_activity(
                "fail_research_activity",
                request,
                task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=30),
                schedule_to_close_timeout=timedelta(seconds=60),
                retry_policy=_RESEARCH_RETRY,
            )
            raise
        return {
            "schema_version": RESEARCH_WORKFLOW_SCHEMA_VERSION,
            "run_id": request.run_id,
            "outcome": "report_published",
        }
