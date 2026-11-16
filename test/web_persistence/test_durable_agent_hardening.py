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


def test_expired_same_run_acquires_new_fence_and_rejects_old_interval(
    db, account_id, database_url, worker_database_url
):
    from run_domain.execution import StaleFencingToken

    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=30)
    first = store.acquire_lease(str(account_id), str(run_id))
    duplicate = store.acquire_lease(str(account_id), str(run_id))
    assert duplicate.fencing_token == first.fencing_token
    db.execute(
        "UPDATE execution_attempt_leases SET lease_expires_at=now()-interval '1 second' "
        "WHERE account_id=%s", (account_id,),
    )
    resumed = store.acquire_lease(str(account_id), str(run_id))
    assert resumed.fencing_token > first.fencing_token
    with pytest.raises(StaleFencingToken):
        store.validate_and_renew_lease(str(account_id), str(run_id), first.fencing_token)
    assert not store.release_lease(str(account_id), str(run_id), first.fencing_token)
    store.validate_and_renew_lease(str(account_id), str(run_id), resumed.fencing_token)
    assert store.release_lease(str(account_id), str(run_id), resumed.fencing_token)
    next_interval = store.acquire_lease(str(account_id), str(run_id))
    assert next_interval.fencing_token > resumed.fencing_token


@pytest.mark.parametrize('extra', [0, 1])
def test_tool_p4_json_boundary_publication_receipt_and_replay(
    tmp_path, db, account_id, database_url, worker_database_url, extra
):
    import json
    from types import SimpleNamespace
    from uuid import UUID

    from actions.contracts import ActionRequest, ActionResult
    from actions.runtime import ActionRuntime
    from agent_activities.runtime import DurableAgentActivities
    from agent_workflows.ids import root_execution_id
    from file_runtime import OutputPublisher
    from resources.run_budget import RunBudgetService
    from run_domain.execution import StaleFencingToken
    from run_domain.execution_context import fence_scope
    from storage.tenant_file_store import TenantFileStore
    from web_domain.file_services import FileService

    _, conversation, run = _conversation_and_run(database_url, account_id)
    from application.chat.service import ChatCommandService
    ChatCommandService(worker_database_url).start_run(account_id, run)
    store = AgentDataStore(worker_database_url)
    lease = store.acquire_lease(str(account_id), str(run))
    execution = str(root_execution_id(run))
    operation, transcript = f'tool:{uuid4()}', f'transcript:{uuid4()}'
    storage = TenantFileStore(tmp_path / 'store', max_bytes=4 * 1048576)
    publisher = OutputPublisher(worker_database_url, storage)
    activity = object.__new__(DurableAgentActivities)
    activity.store, activity.output_publisher = store, publisher
    activity.run_budget = RunBudgetService(worker_database_url)
    request = SimpleNamespace(account_id=str(account_id), run_id=str(run),
                              lease_token=lease.fencing_token, operation_id=operation)
    raw = {'success': True, 'output': {'command_receipt': {'command_id': 'committed-command'},
           'body': ''}, 'metadata': {'escaped': '中"\\\n'}}
    raw['output']['body'] = 'x' * (1048576 - len(json.dumps(raw).encode()) + extra)
    original = ActionResult.from_runtime_result(ActionRequest('call', 'command', {}), raw)
    with fence_scope(str(account_id), str(run), lease.fencing_token, execution):
        context_operation = f'context:{uuid4()}'
        store.begin_operation(context_operation, str(run), 'context')
        store.create_transcript(transcript_id=transcript, run_id=str(run), account_id=str(account_id),
            conversation_id=str(conversation), execution_id=execution, messages=[], operation_id=context_operation)
        store.begin_tool_operation(operation, str(run))
        result = ActionRuntime.prepare_result(original, lambda value, size: activity._publish_tool_raw(request, value, size))
        details = result.metadata['result_processing']
        assert details['raw_json_bytes'] == 1048576 + extra
        assert details['raw_available'] is True
        observation = result.to_observation()
        event = {'message': {'role': 'tool', 'tool_call_id': 'call', 'content': observation},
                 'raw_result': result.raw}
        receipt = {'operation_id': operation, 'result_ref': f'agent-tool-result:{operation}', 'tool_success': True}
        arguments = dict(transcript_id=transcript, expected_version=1, event_type='tool_result',
                         operation_id=operation, event_payload=event, result_ref=receipt['result_ref'], result_payload=receipt)
        assert store.complete_operation_with_event(**arguments) == 2
        assert store.complete_operation_with_event(**arguments) == 2
        replay = store.begin_tool_operation(operation, str(run))
        assert replay.status == 'completed' and replay.result_payload['tool_success'] is True
        messages, version = store.load_messages(transcript)
        assert version == 2 and messages[-1]['content'] == observation
        persisted = db.execute('SELECT payload FROM agent_transcript_events WHERE operation_id=%s', (operation,)).fetchone()[0]
        assert persisted == event
        assert len(json.dumps(persisted['raw_result']).encode()) <= 1048576
        if extra:
            file_id = UUID(details['raw_file_id'])
            files = FileService(database_url, storage, max_bytes=4 * 1048576)
            _, stream = files.download(account_id, file_id)
            with stream:
                assert json.load(stream) == raw
            from workspace.file_scope import RunFileWorkspace
            workspace = RunFileWorkspace(worker_database_url, storage, tmp_path / 'executions')
            with workspace.prepare(account_id, run) as scope:
                scope.authorize(file_id)
                with (scope.outputs_root / details['raw_file_name']).open() as stream:
                    assert json.load(stream) == raw
            # Existing immutable publication and budget receipts deduplicate together.
            repeated = ActionRuntime.prepare_result(original, lambda value, size: activity._publish_tool_raw(request, value, size))
            assert repeated.metadata['result_processing']['raw_file_id'] == str(file_id)
            assert db.execute('SELECT count(*) FROM run_files WHERE run_id=%s AND direction=\'output\'', (run,)).fetchone()[0] == 1
            other = uuid4()
            db.execute('INSERT INTO accounts(account_id) VALUES (%s)', (other,))
            from web_domain.errors import ResourceNotFound
            with pytest.raises(ResourceNotFound):
                files.download(other, file_id)
    db.execute("UPDATE execution_attempt_leases SET lease_expires_at=now()-interval '1 second' WHERE execution_id=%s", (UUID(execution),))
    successor = store.acquire_lease(str(account_id), str(run))
    assert successor.fencing_token > lease.fencing_token
    with fence_scope(str(account_id), str(run), lease.fencing_token, execution):
        if extra:
            fenced = ActionRuntime.prepare_result(original, lambda value, size: activity._publish_tool_raw(request, value, size))
            assert fenced.metadata['result_processing']['raw_available'] is False
            assert 'raw_file_id' not in fenced.metadata['result_processing']
        with pytest.raises(StaleFencingToken):
            store.complete_operation_with_event(**arguments)
