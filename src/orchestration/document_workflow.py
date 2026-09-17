"""Durable routing for heavy document normalization."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from document_activities.contracts import NormalizedDocumentRef, NormalizeDocumentInput
from orchestration.document_contracts import DOCUMENT_TASK_QUEUE


@workflow.defn
class NormalizeDocumentWorkflow:
    @workflow.run
    async def run(self, request: NormalizeDocumentInput) -> NormalizedDocumentRef:
        return await workflow.execute_activity(
            "normalize_document_activity",
            request,
            task_queue=DOCUMENT_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
