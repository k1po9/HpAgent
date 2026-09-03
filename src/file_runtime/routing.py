"""Deterministic routing from ordinary file reads to document normalization."""
from __future__ import annotations

from typing import Any

from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from document_activities.contracts import NormalizedDocumentRef, NormalizeDocumentInput
from file_domain.models import FileResource
from orchestration.document_workflow import NormalizeDocumentWorkflow
from orchestration.web_workflow import WEB_LIFECYCLE_TASK_QUEUE

_COMPLEX_MEDIA_TYPES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
})


class TemporalDocumentRouter:
    def __init__(
        self, client: Any, *, direct_read_max_bytes: int = 1024 * 1024,
        workflow_task_queue: str = WEB_LIFECYCLE_TASK_QUEUE,
    ) -> None:
        if direct_read_max_bytes < 1:
            raise ValueError("direct_read_max_bytes must be positive")
        self.client = client
        self.direct_read_max_bytes = direct_read_max_bytes
        self.workflow_task_queue = workflow_task_queue

    def should_normalize(self, resource: FileResource) -> bool:
        return (
            resource.size_bytes > self.direct_read_max_bytes
            or resource.media_type in _COMPLEX_MEDIA_TYPES
        )

    async def normalize(
        self, account_id: str, run_id: str, resource: FileResource
    ) -> NormalizedDocumentRef:
        operation_id = f"file-normalize:{run_id}:{resource.file_id}"
        workflow_id = f"hpagent-document-{run_id}-{resource.file_id}"
        request = NormalizeDocumentInput(
            1, account_id, run_id, str(resource.file_id), operation_id
        )
        try:
            handle = await self.client.start_workflow(
                NormalizeDocumentWorkflow.run,
                request,
                id=workflow_id,
                task_queue=self.workflow_task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            handle = self.client.get_workflow_handle(workflow_id)
        return await handle.result()
