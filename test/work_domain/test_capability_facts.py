from contextlib import contextmanager
from contextvars import Context
from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest
from jsonschema.exceptions import ValidationError
from test_capability_contracts import definition, scope

from agent_activities.store import AgentDataStore
from agent_workflows.lifecycle_contracts import SegmentInput
from conversation_domain.commands import CommandService
from persistence.uow import UnitOfWork
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import (
    CapabilityObservation,
    CapabilityRegistry,
    CapabilityResult,
    Check,
    ResultRef,
)
from run_domain.execution import StaleFencingToken
from run_domain.execution_context import execution_fence, fence_scope
from run_domain.lifecycle import RunLifecycleService
from run_domain.results import complete_operation
from web_domain.file_lifecycle import claim_file_deletion
from work_domain.models import Requirement
from workspace.discovery import WorkspaceDiscovery
from workspace.resources import ResourceDenied

pytestmark = pytest.mark.postgres


@pytest.fixture
def facts():
    return CapabilityFacts(CapabilityRegistry((definition(),)))


def test_owner_projection_keeps_each_invocation_and_rejects_other_account(facts, urls, account_id, owner):
    from run_domain.queries import capability_results
    from web_domain.errors import ResourceNotFound

    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, _, _):
        first = begin(facts, urls, store, run, 'first')
        second = begin(facts, urls, store, run, 'second')
    publish(facts, urls, owner, account_id, run, first)
    publish(facts, urls, owner, account_id, run, second)
    result = capability_results(urls[1], account_id, run)
    assert {r['invocation_id'] for r in result['items']} == {first['invocation_id'], second['invocation_id']}
    assert not result['truncated']
    assert all(r['payload']['outcome'] == 'insufficient' for r in result['items'])
    with pytest.raises(ResourceNotFound):
        capability_results(urls[1], uuid4(), run)


def chat(urls, account_id):
    commands = CommandService(urls[1])
    cid = UUID(commands.create_conversation(account_id, str(uuid4())).body["conversation_id"])
    run = UUID(commands.send_message(account_id, cid, str(uuid4()), "test capability")["run_id"])
    RunLifecycleService(urls[2]).start(account_id, run)
    return run


@contextmanager
def execution(urls, account_id, run_id, execution_id=None):
    store = AgentDataStore(urls[2])
    if execution_id is None:
        execution_id = store.run_identity(str(run_id))["execution_id"]
    request = SegmentInput(
        2, str(run_id), str(account_id), str(uuid4()), execution_id=str(execution_id)
    )
    token = store.acquire_segment(request)
    try:
        with fence_scope(str(account_id), str(run_id), token, str(execution_id)):
            yield store, request, token
    finally:
        store.release_segment(request)


def identity(operation):
    fence = execution_fence.get()
    return f"{fence[1]}:{operation}" if fence is not None and ":" not in operation else operation


def create(facts, urls, operation="caller", **fields):
    operation = identity(operation)
    with UnitOfWork(urls[2]) as uow:
        return facts.create(
            uow,
            operation_id=operation,
            capability_key="test.echo",
            contract_version=1,
            inputs=fields.get("inputs", {"text": "hello"}),
            scope=fields.get("scope", scope()),
            invocation_limits=definition().limits,
        )


def begin(facts, urls, store, run_id, operation="caller", **fields):
    store.begin_operation(identity(operation), str(run_id), "tool")
    return create(facts, urls, operation, **fields)


def output_file(owner, account_id, run_id):
    file_id = uuid4()
    owner.execute(
        "INSERT INTO stored_files(file_id,account_id,purpose,source_run_id,status,original_name,"
        "display_name,storage_key,content_type,size_bytes,sha256,ready_at) "
        "VALUES (%s,%s,'output',%s,'ready','test.md','test.md',%s,'text/markdown',4,%s,now())",
        (file_id, account_id, run_id, str(file_id), "a" * 64),
    )
    return file_id


def publish(facts, urls, owner, account_id, run_id, invocation, *, outcome="insufficient"):
    file = output_file(owner, account_id, run_id)
    evidence_file = output_file(owner, account_id, run_id)
    child_id = invocation["child_execution_id"]
    producer = f"{invocation['invocation_id']}:producer"
    ref = ResultRef(
        str(uuid4()),
        "markdown",
        str(file),
        str(file),
        invocation["invocation_id"],
        str(child_id),
        producer,
        "a" * 64,
    )
    evidence = replace(
        ref,
        ref_id=str(uuid4()),
        type="check_evidence",
        object_id=str(evidence_file),
        version=str(evidence_file),
    )
    check = Check("test.structure", "1", ref.ref_id, "failed", evidence.ref_id)
    result = CapabilityResult(outcome, "Draft, structure check failed", (ref, evidence), (check,))
    with execution(urls, account_id, run_id, child_id) as (store, _, _):
        store.begin_operation(producer, str(run_id), "result")
        with UnitOfWork(urls[2]) as uow:
            facts.publish_result(
                uow,
                invocation_id=invocation["invocation_id"],
                producing_operation_id=producer,
                result=result,
            )
        # A retry returns exactly the durable result, without another producer.
        with UnitOfWork(urls[2]) as uow:
            facts.publish_result(
                uow,
                invocation_id=invocation["invocation_id"],
                producing_operation_id=producer,
                result=result,
            )
        with UnitOfWork(urls[2]) as uow:
            with pytest.raises(ValueError, match="conflict"):
                facts.publish_result(
                    uow,
                    invocation_id=invocation["invocation_id"],
                    producing_operation_id=producer,
                    result=replace(result, summary="changed"),
                )
    return ref, result, producer


def test_create_dedup_conflict_root_only_and_explicit_fence(facts, urls, account_id, owner):
    run = chat(urls, account_id)
    with pytest.raises(StaleFencingToken):
        create(facts, urls)
    with execution(urls, account_id, run) as (store, _, _):
        first = begin(facts, urls, store, run)
        assert create(facts, urls)["child_execution_id"] == first["child_execution_id"]
        with pytest.raises(ValueError, match="conflict"):
            create(facts, urls, inputs={"text": "different"})
        with pytest.raises(ValidationError):
            create(facts, urls, inputs={"extra": "bad"})
        with pytest.raises(ResourceDenied):
            create(facts, urls, operation="unregistered")
        second = begin(facts, urls, store, run, "caller-2")
        assert second["child_execution_id"] != first["child_execution_id"]
        with pytest.raises(ResourceDenied):
            begin(facts, urls, store, run, "caller-3", scope=scope(tool_names=["host_bash"]))
    with execution(urls, account_id, run, first["child_execution_id"]) as (store, _, _):
        store.begin_operation(identity("child-recursive"), str(run), "tool")
        with pytest.raises(ResourceDenied, match="only root"):
            create(facts, urls, "child-recursive")
        from run_domain.delegation import require_root_write

        with UnitOfWork(urls[2]) as uow:
            with pytest.raises(ValueError, match="Child Execution"):
                require_root_write(uow)
    assert (
        owner.execute(
            "SELECT count(*) n FROM capability_invocations WHERE run_id=%s", (run,)
        ).fetchone()["n"]
        == 2
    )


def test_result_is_not_caller_completion_gc_and_atomic_observation(facts, urls, account_id, owner):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, _, _):
        invocation = begin(facts, urls, store, run)
    ref, result, producer = publish(facts, urls, owner, account_id, run, invocation)
    assert (
        owner.execute(
            "SELECT status FROM execution_operations WHERE operation_id=%s",
            (invocation["invocation_id"],),
        ).fetchone()["status"]
        == "started"
    )
    assert (
        owner.execute(
            "SELECT result_ref FROM run_executions WHERE execution_id=%s",
            (invocation["child_execution_id"],),
        ).fetchone()["result_ref"]
        == f"capability-result:{invocation['invocation_id']}"
    )
    assert (
        owner.execute("SELECT status FROM runs WHERE run_id=%s", (run,)).fetchone()["status"]
        == "running"
    )
    # No run_files or Workspace entry retain this candidate: the typed result edge does.
    with UnitOfWork(urls[2]) as uow:
        assert claim_file_deletion(uow, account_id, UUID(ref.object_id)) == "bound"
    assert (
        WorkspaceDiscovery(urls[1]).retention(account_id, UUID(ref.object_id))["references"][
            "capability_results"
        ]
        == 1
    )
    with execution(urls, account_id, run) as (store, request, _):
        # Resumption registers a new caller attempt; producer work is already durable.
        store.begin_operation(invocation["invocation_id"], str(run), "tool")
        with UnitOfWork(urls[2]) as uow:
            payload = facts.read_result(uow, invocation["invocation_id"])
            assert payload["outcome"] == "insufficient"
            assert payload["provenance"] == {
                "schema_version": 1,
                "invocation_id": invocation["invocation_id"],
                "execution_id": str(invocation["child_execution_id"]),
                "operation_id": producer,
            }
            assert facts.read_ref(uow, UUID(ref.ref_id)).object_id == ref.object_id
        transcript = f"t:{run}"
        store.begin_operation(identity("context"), str(run), "context")
        store.create_transcript(
            transcript_id=transcript,
            run_id=str(run),
            account_id=str(account_id),
            execution_id=request.execution_id,
            conversation_id=store.run_identity(str(run))["conversation_id"],
            messages=[{"role": "user", "content": "test"}],
            operation_id=identity("context"),
        )
        with pytest.raises(RuntimeError, match="rollback"):
            with UnitOfWork(urls[2]) as uow:
                observation(facts, uow, transcript, producer, result)
                raise RuntimeError("rollback")
        assert (
            owner.execute(
                "SELECT count(*) n FROM capability_observations WHERE invocation_id=%s",
                (invocation["invocation_id"],),
            ).fetchone()["n"]
            == 0
        )
        with UnitOfWork(urls[2]) as uow:
            observation(facts, uow, transcript, producer, result)
        with UnitOfWork(urls[2]) as uow:
            facts.record_observation(
                uow,
                invocation_id=invocation["invocation_id"],
                producing_operation_id=producer,
                transcript_id=transcript,
                sequence=2,
            )
        assert (
            owner.execute(
                "SELECT count(*) n FROM capability_observations WHERE invocation_id=%s",
                (invocation["invocation_id"],),
            ).fetchone()["n"]
            == 1
        )


def observation(facts, uow, transcript, producer, result):
    from psycopg.types.json import Jsonb

    invocation_id = producer.removesuffix(":producer")
    link = CapabilityObservation(
        invocation_id, producer, f"capability-result:{invocation_id}"
    ).to_dict()
    payload = {"capability_observation": link}
    uow.execute(
        "INSERT INTO agent_transcript_events(transcript_id,sequence,event_type,operation_id,payload) "
        "VALUES (%s,2,'tool_result',%s,%s)",
        (transcript, invocation_id, Jsonb({"capability_observation": link})),
    )
    uow.execute("UPDATE agent_transcripts SET version=2 WHERE transcript_id=%s", (transcript,))
    complete_operation(uow, invocation_id, f"capability-observation:{invocation_id}", payload)
    facts.record_observation(
        uow,
        invocation_id=invocation_id,
        producing_operation_id=producer,
        transcript_id=transcript,
        sequence=2,
    )


def test_account_run_parent_and_producer_links_are_enforced(facts, urls, account_id, owner):
    run = chat(urls, account_id)
    other_run = chat(urls, account_id)
    other_account = uuid4()
    owner.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other_account,))
    with execution(urls, account_id, run) as (store, _, _):
        invocation = begin(facts, urls, store, run)
    ref, _, producer = publish(facts, urls, owner, account_id, run, invocation)
    # Each wrong ownership tuple fails below the Python boundary as well.
    for column, value in [
        ("account_id", other_account),
        ("run_id", other_run),
        ("parent_execution_id", uuid4()),
    ]:
        with pytest.raises(psycopg.Error):
            owner.execute(
                f"UPDATE run_executions SET {column}=%s WHERE execution_id=%s",
                (value, invocation["child_execution_id"]),
            )
    with execution(urls, account_id, other_run) as (store, _, _):
        with pytest.raises(ResourceDenied):
            create(facts, urls, "caller")
        with UnitOfWork(urls[2]) as uow:
            with pytest.raises(ResourceDenied):
                facts.read_ref(uow, UUID(ref.ref_id))
    foreign = chat(urls, other_account)
    with execution(urls, other_account, foreign):
        with UnitOfWork(urls[2]) as uow:
            with pytest.raises(ResourceDenied):
                facts.read_ref(uow, UUID(ref.ref_id))
            with pytest.raises(ResourceDenied):
                facts.adopt(uow, UUID(ref.ref_id))
    with pytest.raises(psycopg.Error):
        owner.execute(
            "INSERT INTO capability_results(invocation_id,account_id,run_id,producing_execution_id,"
            'producing_operation_id,payload) VALUES (%s,%s,%s,%s,%s,\'{"schema_version":1,"outcome":"failed"}\')',
            ("caller:other", account_id, other_run, invocation["caller_execution_id"], producer),
        )
    with pytest.raises(psycopg.Error):
        owner.execute("DELETE FROM capability_result_refs WHERE ref_id=%s", (UUID(ref.ref_id),))


@pytest.mark.parametrize("action", ["pause", "revise", "stop"])
def test_stale_parent_and_child_cannot_publish_or_adopt(
    facts, urls, account_id, owner, commands, action
):
    requirement = Requirement("Test", "generic_work", {"schema_version": 1})
    accepted = commands.accept(account_id, str(uuid4()), "Test", requirement)
    work_id = UUID(accepted.body["work"]["work_id"])
    admitted = commands.advance(account_id, work_id, str(uuid4()), 1)
    run = UUID(admitted.body["run"]["run_id"])
    with execution(urls, account_id, run) as (store, request, token):
        invocation = begin(facts, urls, store, run, f"caller:{run}")
    with execution(urls, account_id, run, invocation["child_execution_id"]) as (
        store,
        child_request,
        child_token,
    ):
        producer = f"producer:{run}"
        store.begin_operation(producer, str(run), "result")
        current = commands.get(account_id, work_id)["work"]
        if action == "revise":
            Context().run(
                commands.revise,
                account_id,
                work_id,
                str(uuid4()),
                current["row_version"],
                requirement,
            )
        else:
            Context().run(
                commands.control, account_id, work_id, str(uuid4()), current["row_version"], action
            )
        with UnitOfWork(urls[2]) as uow:
            with pytest.raises(StaleFencingToken):
                facts.publish_result(
                    uow,
                    invocation_id=invocation["invocation_id"],
                    producing_operation_id=producer,
                    result=CapabilityResult("succeeded", "late"),
                )
    with fence_scope(str(account_id), str(run), token, request.execution_id):
        with pytest.raises(StaleFencingToken):
            create(facts, urls, invocation["invocation_id"])
        with UnitOfWork(urls[2]) as uow:
            with pytest.raises(StaleFencingToken):
                facts.adopt(uow, uuid4())
    assert (
        owner.execute(
            "SELECT count(*) n FROM capability_results WHERE run_id=%s", (run,)
        ).fetchone()["n"]
        == 0
    )
    assert (
        owner.execute(
            "SELECT status FROM run_executions WHERE execution_id=%s",
            (UUID(child_request.execution_id),),
        ).fetchone()["status"]
        == "cancelling"
    )


def test_old_attempt_and_role_privileges(facts, urls, owner, account_id):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, old, old_token):
        invocation = begin(facts, urls, store, run, f"caller:{run}")
    with execution(urls, account_id, run):
        with fence_scope(str(account_id), str(run), old_token, old.execution_id):
            with pytest.raises(StaleFencingToken):
                create(facts, urls, invocation["invocation_id"])
    with psycopg.connect(urls[1]) as connection:
        for table in (
            "capability_invocations",
            "capability_results",
            "capability_result_refs",
            "capability_result_checks",
            "capability_result_adoptions",
            "capability_observations",
        ):
            assert connection.execute(
                "SELECT has_table_privilege(current_user,%s,'SELECT')", (f"hpagent.{table}",)
            ).fetchone()[0]
            assert not connection.execute(
                "SELECT has_table_privilege(current_user,%s,'INSERT')", (f"hpagent.{table}",)
            ).fetchone()[0]
    with psycopg.connect(urls[2]) as connection:
        assert not connection.execute(
            "SELECT has_table_privilege(current_user,'hpagent.capability_results','UPDATE')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT has_table_privilege(current_user,'hpagent.capability_result_refs','DELETE')"
        ).fetchone()[0]


def test_adoption_fixed_to_consumer_and_rechecks_revocation(facts, urls, account_id, owner):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, _, _):
        invocation = begin(facts, urls, store, run, f"caller:{run}")
    ref, _, _ = publish(facts, urls, owner, account_id, run, invocation)
    other = chat(urls, account_id)
    # Explicit attachment is an existing trusted input path; account ownership alone was denied above.
    cid = owner.execute("SELECT conversation_id FROM runs WHERE run_id=%s", (other,)).fetchone()[
        "conversation_id"
    ]
    owner.execute(
        "INSERT INTO conversation_resource_files(account_id,conversation_id,file_id) VALUES (%s,%s,%s)",
        (account_id, cid, UUID(ref.object_id)),
    )
    owner.execute(
        "INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,basis) VALUES (%s,%s,%s,%s,'explicit_attachment')",
        (uuid4(), account_id, other, UUID(ref.object_id)),
    )
    with execution(urls, account_id, other) as (store, _, _):
        with UnitOfWork(urls[2]) as uow:
            first = facts.adopt(uow, UUID(ref.ref_id))
            assert facts.adopt(uow, UUID(ref.ref_id)) == first
            assert (
                facts.read_ref(uow, UUID(ref.ref_id)).producing_invocation_id
                == invocation["invocation_id"]
            )
        child = begin(
            facts, urls, store, other, f"consumer:{other}", scope=scope(result_ids=[ref.ref_id])
        )
        with execution(urls, account_id, other, child["child_execution_id"]):
            with UnitOfWork(urls[2]) as uow:
                assert facts.read_ref(uow, UUID(ref.ref_id)).ref_id == ref.ref_id
        owner.execute(
            "UPDATE conversation_resource_files SET available=false WHERE account_id=%s AND conversation_id=%s AND file_id=%s",
            (account_id, cid, UUID(ref.object_id)),
        )
        with UnitOfWork(urls[2]) as uow:
            with pytest.raises(ResourceDenied, match="authorization"):
                facts.read_ref(uow, UUID(ref.ref_id))
        with execution(urls, account_id, other, child["child_execution_id"]):
            with UnitOfWork(urls[2]) as uow:
                with pytest.raises(ResourceDenied, match="authorization"):
                    facts.read_ref(uow, UUID(ref.ref_id))
    assert (
        owner.execute(
            "SELECT run_id FROM capability_results WHERE invocation_id=%s",
            (invocation["invocation_id"],),
        ).fetchone()["run_id"]
        == run
    )


def test_sibling_reads_only_explicit_result_subset(facts, urls, owner, account_id):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, _, _):
        invocation = begin(facts, urls, store, run, f"first:{run}")
    ref, result, _ = publish(facts, urls, owner, account_id, run, invocation)
    with execution(urls, account_id, run) as (store, _, _):
        second = begin(
            facts, urls, store, run, f"second:{run}", scope=scope(result_ids=[ref.ref_id])
        )
        third = begin(facts, urls, store, run, f"third:{run}")
    with execution(urls, account_id, run, second["child_execution_id"]):
        with UnitOfWork(urls[2]) as uow:
            assert facts.read_ref(uow, UUID(ref.ref_id)).ref_id == ref.ref_id
            with pytest.raises(ResourceDenied):
                facts.read_ref(uow, UUID(result.outputs[1].ref_id))
    with execution(urls, account_id, run, third["child_execution_id"]):
        with UnitOfWork(urls[2]) as uow:
            with pytest.raises(ResourceDenied):
                facts.read_ref(uow, UUID(ref.ref_id))


def test_replaced_child_attempt_cannot_commit_new_result(facts, urls, owner, account_id):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, _, _):
        invocation = begin(facts, urls, store, run, f"caller:{run}")
    child = invocation["child_execution_id"]
    producer = f"producer:{run}"
    with execution(urls, account_id, run, child) as (store, old, old_token):
        store.begin_operation(producer, str(run), "result")
    with execution(urls, account_id, run, child) as (store, _, _):
        store.begin_operation(producer, str(run), "result")
        with fence_scope(str(account_id), str(run), old_token, old.execution_id):
            with UnitOfWork(urls[2]) as uow:
                with pytest.raises(StaleFencingToken):
                    facts.publish_result(
                        uow,
                        invocation_id=invocation["invocation_id"],
                        producing_operation_id=producer,
                        result=CapabilityResult("failed", "old attempt"),
                    )
        with UnitOfWork(urls[2]) as uow:
            facts.publish_result(
                uow,
                invocation_id=invocation["invocation_id"],
                producing_operation_id=producer,
                result=CapabilityResult("uncertain", "reconcile"),
            )
    assert (
        owner.execute(
            "SELECT payload->>'outcome' outcome FROM capability_results WHERE invocation_id=%s",
            (invocation["invocation_id"],),
        ).fetchone()["outcome"]
        == "uncertain"
    )


def test_artifact_ref_fixes_exact_version_and_child_producer(facts, urls, owner, account_id):
    from web_artifacts.publisher import publish as publish_artifact

    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, _, _):
        invocation = begin(facts, urls, store, run, f"caller:{run}")
    artifact_id, version_id = uuid4(), uuid4()
    producer = f"artifact:{run}"
    ref = ResultRef(
        str(uuid4()),
        "artifact_version",
        str(artifact_id),
        str(version_id),
        invocation["invocation_id"],
        str(invocation["child_execution_id"]),
        producer,
    )
    with execution(urls, account_id, run, invocation["child_execution_id"]) as (store, _, _):
        store.begin_operation(producer, str(run), "result")
        with UnitOfWork(urls[2]) as uow:
            publish_artifact(
                uow,
                run,
                artifact_id,
                version_id,
                "<!doctype html><title>Fixture</title>",
                operation_id=producer,
            )
            facts.publish_result(
                uow,
                invocation_id=invocation["invocation_id"],
                producing_operation_id=producer,
                result=CapabilityResult("succeeded", "Fixture", (ref,)),
            )
    with execution(urls, account_id, run):
        with UnitOfWork(urls[2]) as uow:
            assert facts.read_ref(uow, UUID(ref.ref_id)).version == str(version_id)
    with pytest.raises(psycopg.Error):
        owner.execute("DELETE FROM artifact_versions WHERE artifact_version_id=%s", (version_id,))


def test_gc_claim_prevents_partial_result_commit(facts, urls, owner, account_id):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, _, _):
        invocation = begin(facts, urls, store, run, f"caller:{run}")
    file_id = output_file(owner, account_id, run)
    with UnitOfWork(urls[2]) as uow:
        assert claim_file_deletion(uow, account_id, file_id) == "deleted"
    producer = f"producer:{run}"
    ref = ResultRef(
        str(uuid4()),
        "markdown",
        str(file_id),
        str(file_id),
        invocation["invocation_id"],
        str(invocation["child_execution_id"]),
        producer,
    )
    with execution(urls, account_id, run, invocation["child_execution_id"]) as (store, _, _):
        store.begin_operation(producer, str(run), "result")
        with pytest.raises(psycopg.Error, match="unavailable"):
            with UnitOfWork(urls[2]) as uow:
                facts.publish_result(
                    uow,
                    invocation_id=invocation["invocation_id"],
                    producing_operation_id=producer,
                    result=CapabilityResult("succeeded", "file lost GC race", (ref,)),
                )
    assert (
        owner.execute(
            "SELECT status FROM execution_operations WHERE operation_id=%s", (producer,)
        ).fetchone()["status"]
        == "started"
    )
    assert (
        owner.execute(
            "SELECT count(*) n FROM capability_results WHERE invocation_id=%s",
            (invocation["invocation_id"],),
        ).fetchone()["n"]
        == 0
    )
