from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import psycopg
import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from orchestration.artifact_activities import (
    execute_artifact_build_activity,
    inject_artifact_build_service,
)
from orchestration.artifact_dispatcher import (
    ArtifactOutboxDispatcher,
    TemporalArtifactClient,
    artifact_workflow_id,
)
from orchestration.artifact_workflow import ARTIFACT_TASK_QUEUE, ArtifactBuildWorkflow
from web_artifacts.build import ArtifactBuildService
from web_artifacts.outbox import ArtifactOutboxService
from web_artifacts.services import ArtifactService
from web_domain.errors import IdempotencyConflict, ResourceNotFound
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


class _Generator:
    async def generate(self, **_kwargs):
        return "<!doctype html><html><head></head><body>ok</body></html>"


def _completed_assistant(account_id, database_url, worker_database_url):
    chat = CommandService(database_url)
    conversation_id = UUID(chat.create_conversation(account_id, str(uuid4()))["conversation_id"])
    sent = chat.send_message(account_id, conversation_id, str(uuid4()), "source")
    run_id = UUID(sent["run_id"])
    worker = CommandService(worker_database_url)
    assert worker.start_run(account_id, run_id)
    assert worker.complete_run(account_id, run_id, "# Dashboard\nA = 1")
    return chat, conversation_id, UUID(sent["assistant_message"]["message_id"])


def test_create_artifact_is_atomic_idempotent_and_does_not_block_chat(
    db, account_id, database_url, worker_database_url
):
    chat, conversation_id, message_id = _completed_assistant(
        account_id, database_url, worker_database_url
    )
    service, key = ArtifactService(database_url), str(uuid4())
    first = service.create_artifact(account_id, message_id, key)
    replay = service.create_artifact(account_id, message_id, key)
    assert first.body == replay.body
    assert replay.replayed is True
    assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM artifact_outbox_events").fetchone()[0] == 1

    # Artifact Version remains queued, but the ordinary conversation lock is free.
    next_chat = chat.send_message(account_id, conversation_id, str(uuid4()), "continue")
    assert next_chat["run"]["status"] == "queued"


def test_artifact_rejects_cross_account_and_idempotency_payload_change(
    db, account_id, database_url, worker_database_url
):
    _, _, message_id = _completed_assistant(account_id, database_url, worker_database_url)
    service, key = ArtifactService(database_url), str(uuid4())
    service.create_artifact(account_id, message_id, key, "one")
    with pytest.raises(IdempotencyConflict):
        service.create_artifact(account_id, message_id, key, "two")
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    with pytest.raises(ResourceNotFound):
        service.create_artifact(other, message_id, str(uuid4()))


def test_user_or_pending_assistant_cannot_be_an_artifact_source(
    db, account_id, database_url
):
    chat = CommandService(database_url)
    conversation_id = UUID(chat.create_conversation(account_id, str(uuid4()))["conversation_id"])
    sent = chat.send_message(account_id, conversation_id, str(uuid4()), "source")
    service = ArtifactService(database_url)
    for field in ("user_message", "assistant_message"):
        with pytest.raises(ValueError, match="artifact_source_invalid"):
            service.create_artifact(
                account_id, UUID(sent[field]["message_id"]), str(uuid4())
            )


@pytest.mark.asyncio
async def test_api_role_can_create_version_and_failed_version_is_not_parent(
    db, account_id, database_url, worker_database_url
):
    _, _, message_id = _completed_assistant(account_id, database_url, worker_database_url)
    api = ArtifactService(database_url)
    created = api.create_artifact(account_id, message_id, str(uuid4()))
    artifact_id = UUID(created["artifact"]["artifact_id"])
    v1_id = UUID(created["version"]["artifact_version_id"])

    # Worker owns build-state transitions; completing v1 also proves its
    # artifact_versions/artifacts UPDATE grants are usable.
    await ArtifactBuildService(worker_database_url, _Generator()).execute(v1_id)

    v2 = api.create_version(account_id, artifact_id, str(uuid4()), "改成柱状图")
    assert v2["version"]["version"] == 2
    assert v2["version"]["parent_version_id"] == str(v1_id)
    v2_id = UUID(v2["version"]["artifact_version_id"])

    with psycopg.connect(worker_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "UPDATE artifact_versions SET status='failed',failure_code='artifact_build_failed',"
            "failure_message='failed',started_at=now(),completed_at=now(),updated_at=now() "
            "WHERE artifact_version_id=%s", (v2_id,),
        )

    v3 = api.create_version(account_id, artifact_id, str(uuid4()), "再修改")
    assert v3["version"]["version"] == 3
    assert v3["version"]["parent_version_id"] == str(v1_id)


@pytest.mark.temporal
@pytest.mark.asyncio
async def test_real_temporal_artifact_outbox_to_persisted_html(
    db, account_id, database_url, worker_database_url
):
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")
    _, _, message_id = _completed_assistant(account_id, database_url, worker_database_url)
    created = ArtifactService(database_url).create_artifact(
        account_id, message_id, str(uuid4())
    )
    version_id = UUID(created["version"]["artifact_version_id"])

    inject_artifact_build_service(
        ArtifactBuildService(worker_database_url, _Generator())
    )
    client = await Client.connect(temporal_host)
    worker = Worker(
        client,
        task_queue=ARTIFACT_TASK_QUEUE,
        workflows=[ArtifactBuildWorkflow],
        activities=[execute_artifact_build_activity],
    )
    dispatcher = ArtifactOutboxDispatcher(
        ArtifactOutboxService(worker_database_url),
        TemporalArtifactClient(client),
        f"artifact-integration-{uuid4()}",
    )

    async with worker:
        assert await dispatcher.run_once() == 1
        result = await asyncio.wait_for(
            client.get_workflow_handle(artifact_workflow_id(version_id)).result(),
            timeout=20,
        )

    assert result == {
        "schema_version": 1,
        "artifact_version_id": str(version_id),
        "status": "completed",
    }
    assert db.execute(
        "SELECT status,html FROM artifact_versions WHERE artifact_version_id=%s",
        (version_id,),
    ).fetchone() == (
        "completed",
        "<!doctype html><html><head></head><body>ok</body></html>",
    )
    assert db.execute(
        "SELECT status FROM artifact_outbox_events WHERE artifact_version_id=%s",
        (version_id,),
    ).fetchone()[0] == "processed"

    # Workflow History contains only the small status receipt, never persisted HTML.
    history = await client.get_workflow_handle(
        artifact_workflow_id(version_id)
    ).fetch_history()
    payload_sizes = [
        len(payload.data)
        for event in history.events
        for payload in (
            event.activity_task_completed_event_attributes.result.payloads
            if event.HasField("activity_task_completed_event_attributes")
            else []
        )
    ]
    assert payload_sizes and max(payload_sizes) < 4096
