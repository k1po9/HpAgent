from __future__ import annotations

from datetime import timedelta
from time import sleep
from uuid import UUID, uuid4

import pytest

from agent_workflows.tool_execution import tool_execution_workflow_id
from file_domain.approvals import (
    ApprovalNotGranted,
    ApprovalNotPending,
    FileActionApprovalService,
)
from web_domain.errors import ResourceNotFound
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


def _run(account_id, database_url, worker_database_url):
    api = CommandService(database_url)
    conversation_id = UUID(api.create_conversation(account_id, str(uuid4()))["conversation_id"])
    run_id = UUID(api.send_message(
        account_id, conversation_id, str(uuid4()), "high risk action"
    )["run_id"])
    CommandService(worker_database_url).start_run(account_id, run_id)
    return conversation_id, run_id


def test_approval_is_owned_idempotent_and_single_use(
    db, account_id, database_url, worker_database_url,
):
    conversation_id, run_id = _run(account_id, database_url, worker_database_url)
    worker = FileActionApprovalService(worker_database_url)
    api = FileActionApprovalService(database_url)
    request = worker.request(
        account_id, conversation_id, run_id, "op-1", "external_file_overwrite",
        "Overwrite an external destination", "a" * 64,
        intent={"logical_path": "research/agent-memory.md", "expected_revision": 1,
                "new_sha256": "b" * 64},
    )
    assert request.status == "pending"
    listed = api.list_for_run(account_id, run_id)[0]
    assert "arguments_hash" not in listed
    assert listed["logical_path"] == "research/agent-memory.md"
    assert listed["expected_revision"] == 1
    assert "new_sha256" not in listed

    key = str(uuid4())
    result = api.decide(account_id, request.approval_id, "approved", key)
    replay = api.decide(account_id, request.approval_id, "approved", key)
    assert result.body["approval"]["status"] == "approved"
    assert replay.replayed is True
    outbox = db.execute(
        "SELECT event_type,business_key,payload FROM hpagent.outbox_events "
        "WHERE event_type='file_action_approval_decided'"
    ).fetchall()
    assert len(outbox) == 1
    assert outbox[0][1] == f"file-approval:{request.approval_id}:approved"
    assert outbox[0][2] == {
        "approval_id": str(request.approval_id),
        "operation_id": "op-1",
        "tool_execution_workflow_id": tool_execution_workflow_id(str(run_id), "op-1"),
        "version": 1,
    }

    assert worker.consume(
        account_id, run_id, "op-1", "external_file_overwrite", "a" * 64,
        execution_id="op-1", fencing_token=7,
    ) == request.approval_id
    assert worker.consume(
        account_id, run_id, "op-1", "external_file_overwrite", "a" * 64,
        execution_id="op-1", fencing_token=7,
    ) == request.approval_id
    with pytest.raises(ApprovalNotGranted):
        worker.consume(
            account_id, run_id, "op-1", "external_file_overwrite", "a" * 64,
            execution_id="op-1", fencing_token=8,
        )


def test_approval_rejects_cross_tenant_access_and_second_decision(
    db, account_id, database_url, worker_database_url,
):
    conversation_id, run_id = _run(account_id, database_url, worker_database_url)
    worker = FileActionApprovalService(worker_database_url)
    api = FileActionApprovalService(database_url)
    request = worker.request(
        account_id, conversation_id, run_id, "op-2", "send_file",
        "Send a file to an external recipient", "b" * 64,
    )
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    with pytest.raises(ResourceNotFound):
        api.decide(other, request.approval_id, "approved", str(uuid4()))
    api.decide(account_id, request.approval_id, "rejected", str(uuid4()))
    with pytest.raises(ApprovalNotPending):
        api.decide(account_id, request.approval_id, "approved", str(uuid4()))
    with pytest.raises(ApprovalNotGranted):
        worker.consume(
            account_id, run_id, "op-2", "send_file", "b" * 64,
            execution_id="op-2", fencing_token=1,
        )


def test_authoritative_expiry_and_cancel_are_terminal_and_compact(
    db, account_id, database_url, worker_database_url,
):
    conversation_id, run_id = _run(account_id, database_url, worker_database_url)
    worker = FileActionApprovalService(worker_database_url)
    expiring = worker.request(
        account_id, conversation_id, run_id, "expire-op", "overwrite", "expire",
        "c" * 64, ttl=timedelta(milliseconds=5),
    )
    sleep(0.02)
    assert worker.authoritative_status(
        account_id, run_id, "expire-op", expiring.approval_id
    ).status == "expired"

    cancelled = worker.request(
        account_id, conversation_id, run_id, "cancel-op", "overwrite", "cancel",
        "d" * 64,
    )
    assert worker.cancel(
        account_id, run_id, "cancel-op", cancelled.approval_id
    ).status == "cancelled"
    payload = db.execute(
        "SELECT payload FROM hpagent.outbox_events "
        "WHERE business_key=%s", (f"file-approval:{cancelled.approval_id}:cancelled",),
    ).fetchone()[0]
    assert set(payload) == {
        "approval_id", "operation_id", "tool_execution_workflow_id", "version"
    }


def test_run_cancel_atomically_cancels_pending_approval_and_enqueues_wake(
    db, account_id, database_url, worker_database_url,
):
    conversation_id, run_id = _run(account_id, database_url, worker_database_url)
    approval = FileActionApprovalService(worker_database_url).request(
        account_id, conversation_id, run_id, "run-cancel-op", "overwrite", "cancel",
        "f" * 64,
    )
    result = CommandService(database_url).cancel_run(account_id, run_id, str(uuid4()))
    assert result.body["status"] == "cancelling"
    assert db.execute(
        "SELECT status FROM hpagent.file_action_approvals WHERE approval_id=%s",
        (approval.approval_id,),
    ).fetchone()[0] == "cancelled"
    assert db.execute(
        "SELECT count(*) FROM hpagent.outbox_events WHERE business_key=%s",
        (f"file-approval:{approval.approval_id}:cancelled",),
    ).fetchone()[0] == 1
