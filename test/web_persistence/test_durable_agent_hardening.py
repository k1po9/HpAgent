from __future__ import annotations

from uuid import uuid4

import pytest

from agent_activities.store import AgentDataStore

from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


def test_tool_operation_intent_and_uncertain_survive_activity_redelivery(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    operation_id = f"test-tool:{uuid4()}"
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=5)

    first = store.begin_tool_operation(operation_id, str(run_id))
    assert first.status == "started"
    intent = {
        "schema_version": 1,
        "tool": "external_write",
        "arguments_hash": "stable",
        "side_effect_class": "non_idempotent_write",
    }
    store.record_operation_intent(operation_id, intent)

    redelivered = store.begin_tool_operation(operation_id, str(run_id))
    assert redelivered.status == "intent_recorded"
    assert redelivered.result_payload == intent

    store.mark_operation_uncertain(operation_id, "worker_crash_after_side_effect")
    uncertain = store.begin_tool_operation(operation_id, str(run_id))
    assert uncertain.status == "uncertain"
    assert uncertain.result_payload == intent

    completed = {
        "schema_version": 1,
        "operation_id": operation_id,
        "result_ref": f"result:{operation_id}",
        "transcript_version": 2,
        "display_summary": "done",
    }
    store.complete_operation(operation_id, completed["result_ref"], completed)
    duplicate = store.begin_tool_operation(operation_id, str(run_id))
    assert duplicate.status == "completed"
    assert duplicate.result_payload == completed
