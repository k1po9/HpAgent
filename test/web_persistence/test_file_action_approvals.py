from __future__ import annotations

from uuid import UUID, uuid4

import pytest

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
    )
    assert request.status == "pending"
    assert "arguments_hash" not in api.list_for_run(account_id, run_id)[0]

    key = str(uuid4())
    result = api.decide(account_id, request.approval_id, "approved", key)
    replay = api.decide(account_id, request.approval_id, "approved", key)
    assert result.body["approval"]["status"] == "approved"
    assert replay.replayed is True

    assert worker.consume(
        account_id, run_id, "op-1", "external_file_overwrite", "a" * 64
    ) == request.approval_id
    with pytest.raises(ApprovalNotGranted):
        worker.consume(
            account_id, run_id, "op-1", "external_file_overwrite", "a" * 64
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
        worker.consume(account_id, run_id, "op-2", "send_file", "b" * 64)
