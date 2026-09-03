"""Dedicated low-concurrency Temporal Worker for Docling normalization."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from document_activities import DocumentActivities
from file_adapters import DoclingStructuredDocumentProvider
from orchestration.document_contracts import DOCUMENT_TASK_QUEUE
from storage.tenant_file_store import TenantFileReader
from workspace.file_scope import RunFileWorkspace


def build_document_worker(client: Client, activities: DocumentActivities) -> Worker:
    return Worker(
        client,
        task_queue=DOCUMENT_TASK_QUEUE,
        activities=[activities.normalize_document],
        max_concurrent_activities=1,
    )


async def main_async() -> None:
    database_url = os.environ["WORKER_DATABASE_URL"]
    store = TenantFileReader(
        Path(os.environ.get("FILE_STORE_ROOT", "/var/lib/hpagent/file-store"))
    )
    workspace = RunFileWorkspace(
        database_url,
        store,
        Path(os.environ.get("DOCUMENT_RUN_ROOT", "/var/lib/hpagent/document-runs")),
    )
    activities = DocumentActivities(
        database_url, workspace, DoclingStructuredDocumentProvider()
    )
    client = await Client.connect(os.environ.get("TEMPORAL_HOST", "temporal:7233"))
    worker = build_document_worker(client, activities)
    logging.getLogger("HpAgent.DocumentWorker").info(
        "Document Worker started on %s with concurrency=1", DOCUMENT_TASK_QUEUE
    )
    await worker.run()


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
