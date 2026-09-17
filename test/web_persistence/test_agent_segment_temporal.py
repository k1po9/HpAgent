import asyncio
import os
from dataclasses import replace

import pytest
from support.segment_workflows import Scenario, SegmentScenarioWorkflow
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker

from agent_activities.fencing import fence_scope
from agent_activities.segments import SegmentActivities
from agent_activities.store import AgentDataStore, StaleFencingToken
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    ModelDecisionInput,
    RunContext,
    RunSource,
)
from agent_workflows.lifecycle_contracts import SegmentInput, SegmentLease

from .test_phase_a_invariants import _conversation_and_run

pytestmark = [pytest.mark.postgres, pytest.mark.temporal, pytest.mark.asyncio]


class Probes:
    def __init__(self, store):
        self.store = store
        self.workspace_lock = asyncio.Lock()
        self.tokens = []
        self.after_effect_crashed = False

    @activity.defn(name="segment_probe_activity")
    async def execute(self, request: ModelDecisionInput) -> int:
        self.tokens.append((request.operation_id, request.lease_token, request.execution_attempt))
        async with self.workspace_lock:
            if request.objective == "block":
                while True:
                    activity.heartbeat("active-segment")
                    await asyncio.sleep(0.05)
            with fence_scope(request.account_id, request.run_id, request.lease_token):
                previous = await asyncio.to_thread(
                    self.store.begin_operation, request.operation_id, request.run_id, "model"
                )
                if previous is not None:
                    return previous["token"]
                await asyncio.to_thread(
                    self.store.validate_and_renew_lease,
                    request.account_id,
                    request.run_id,
                    request.lease_token,
                )
                await asyncio.to_thread(
                    self.store.complete_operation,
                    request.operation_id,
                    f"result:{request.operation_id}",
                    {"token": request.lease_token},
                )
        if request.objective == "crash" and not self.after_effect_crashed:
            self.after_effect_crashed = True
            raise ApplicationError("crash after commit before response")
        return request.lease_token

    @activity.defn(name="segment_ready_activity")
    async def ready(self, run_id: str) -> bool:
        return (
            await asyncio.to_thread(self.store.operation_result, f"authority:{run_id}") is not None
        )


class LostResponses(SegmentActivities):
    def __init__(self, store):
        super().__init__(store)
        self.acquired_once = False
        self.released_once = False

    @activity.defn(name="acquire_agent_segment_activity")
    async def acquire(self, request: SegmentInput) -> SegmentLease:
        result = await super().acquire(request)
        if result.acquired and not self.acquired_once:
            self.acquired_once = True
            raise ApplicationError("acquire committed, response lost")
        return result

    @activity.defn(name="release_agent_segment_activity")
    async def release(self, request: SegmentInput) -> None:
        await super().release(request)
        if not self.released_once:
            self.released_once = True
            raise ApplicationError("release committed, response lost")


async def client():
    if not os.getenv("TEMPORAL_HOST"):
        pytest.skip("TEMPORAL_HOST required")
    return await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.getenv("TEMPORAL_NAMESPACE", "default")
    )


def request(account_id, run_id):
    return ModelDecisionInput(
        schema_version=AGENT_SCHEMA_VERSION,
        run_id=str(run_id),
        account_id=str(account_id),
        source=RunSource("external_task", "task:1"),
        context=RunContext(context_ref="task:1"),
        strategy="react",
        transcript_id="not-used",
        transcript_version=1,
        turn=1,
        operation_id=f"{run_id}:operation",
        lease_token=0,
    )


def worker(client, segments, probes):
    return Worker(
        client,
        task_queue=AGENT_TASK_QUEUE,
        workflows=[SegmentScenarioWorkflow],
        activities=[
            segments.acquire,
            segments.release,
            segments.begin_wait,
            segments.finish_wait,
            probes.execute,
            probes.ready,
        ],
        max_concurrent_activities=1,
    )


async def wait_until_waiting(db, run_id):
    async with asyncio.timeout(15):
        while not db.execute(
            "SELECT 1 FROM agent_run_waits WHERE run_id=%s AND state='waiting'", (run_id,)
        ).fetchone():
            await asyncio.sleep(0.05)


def assert_no_resources(db, account_id, probes):
    assert not probes.workspace_lock.locked()
    assert (
        db.execute(
            "SELECT owner_run_id FROM account_execution_leases WHERE account_id=%s", (account_id,)
        ).fetchone()[0]
        is None
    )
    assert (
        db.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE usename='hpagent_worker' AND state='idle in transaction'"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("reason", ["external_callback", "model_review"])
async def test_wait_longer_than_ttl_restart_duplicate_wakeup_and_replay(
    db, account_id, database_url, worker_database_url, tmp_path, reason
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=2)
    segments, probes, temporal = SegmentActivities(store), Probes(store), await client()
    async with worker(temporal, segments, probes):
        handle = await temporal.start_workflow(
            SegmentScenarioWorkflow.run,
            Scenario(request(account_id, run_id), reason=reason),
            id=f"segment-scenario-{run_id}",
            task_queue=AGENT_TASK_QUEUE,
        )
        await wait_until_waiting(db, run_id)
        assert_no_resources(db, account_id, probes)
        await handle.signal(SegmentScenarioWorkflow.wake, "wrong-wait-id")
        await handle.signal(SegmentScenarioWorkflow.wake, f"{run_id}:wait")
        await asyncio.sleep(0.1)
        assert await handle.query(SegmentScenarioWorkflow.state) == "waiting"
        # Same account, another Conversation/Run can execute on the sole Activity slot.
        _, _, other_run = _conversation_and_run(database_url, account_id)
        sibling = await temporal.execute_workflow(
            SegmentScenarioWorkflow.run,
            Scenario(request(account_id, other_run), "timer", 0),
            id=f"sibling-{other_run}",
            task_queue=AGENT_TASK_QUEUE,
        )
        assert sibling[1] > sibling[0]
    await asyncio.sleep(2.1)  # longer than the test execution lease TTL
    assert_no_resources(db, account_id, probes)
    store.begin_operation(f"authority:{run_id}", str(run_id), "model")
    store.complete_operation(f"authority:{run_id}", f"approval:{run_id}", {"ready": True})
    async with worker(temporal, segments, probes):
        await handle.signal(SegmentScenarioWorkflow.wake, f"{run_id}:wait")
        await handle.signal(SegmentScenarioWorkflow.wake, f"{run_id}:wait")
        result = await asyncio.wait_for(handle.result(), 15)
    assert result[1] > result[0]
    assert len([x for x in probes.tokens if x[0] == f"{run_id}:after"]) == 1
    with pytest.raises(StaleFencingToken):
        store.validate_and_renew_lease(str(account_id), str(run_id), result[0])
    assert_no_resources(db, account_id, probes)
    history = await handle.fetch_history()
    (tmp_path / "segment-completed-history.json").write_text(history.to_json())
    replay = await Replayer(workflows=[SegmentScenarioWorkflow]).replay_workflow(history)
    assert replay.replay_failure is None


async def test_crash_and_lost_control_responses_reacquire_without_duplicate_commit(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=10)
    segments, probes, temporal = LostResponses(store), Probes(store), await client()
    async with worker(temporal, segments, probes):
        result = await asyncio.wait_for(
            temporal.execute_workflow(
                SegmentScenarioWorkflow.run,
                Scenario(replace(request(account_id, run_id), objective="crash"), "timer", 0),
                id=f"crash-segment-{run_id}",
                task_queue=AGENT_TASK_QUEUE,
            ),
            30,
        )
    first_attempts = [x for x in probes.tokens if x[0] == f"{run_id}:first"]
    assert len(first_attempts) == 2
    assert first_attempts[1][1] > first_attempts[0][1]
    assert [x[2] for x in first_attempts] == [1, 2]
    assert result[0] == first_attempts[0][1]  # committed result reused
    assert result[1] > first_attempts[1][1]
    assert_no_resources(db, account_id, probes)


@pytest.mark.parametrize("cancel_mode", ["workflow", "pg"])
async def test_cancel_during_wait_prevents_resume_and_replays(
    db, account_id, database_url, worker_database_url, cancel_mode
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=2)
    segments, probes, temporal = SegmentActivities(store), Probes(store), await client()
    async with worker(temporal, segments, probes):
        handle = await temporal.start_workflow(
            SegmentScenarioWorkflow.run,
            Scenario(request(account_id, run_id)),
            id=f"cancel-segment-{run_id}",
            task_queue=AGENT_TASK_QUEUE,
        )
        await wait_until_waiting(db, run_id)
        if cancel_mode == "workflow":
            await handle.cancel()
        else:
            db.execute("UPDATE runs SET status='cancelling' WHERE run_id=%s", (run_id,))
            store.begin_operation(f"authority:{run_id}", str(run_id), "model")
            store.complete_operation(f"authority:{run_id}", f"approval:{run_id}", {"ready": True})
            await handle.signal(SegmentScenarioWorkflow.wake, f"{run_id}:wait")
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), 15)
    assert not any(x[0] == f"{run_id}:after" for x in probes.tokens)
    assert (
        db.execute("SELECT state FROM agent_run_waits WHERE run_id=%s", (run_id,)).fetchone()[0]
        == "cancelled"
    )
    assert_no_resources(db, account_id, probes)
    replay = await Replayer(workflows=[SegmentScenarioWorkflow]).replay_workflow(
        await handle.fetch_history()
    )
    assert replay.replay_failure is None


class ExpiredResponse(SegmentActivities):
    def __init__(self, store):
        super().__init__(store)
        self.expired_once = False

    @activity.defn(name="acquire_agent_segment_activity")
    async def acquire(self, request: SegmentInput) -> SegmentLease:
        result = await super().acquire(request)
        if result.acquired and not self.expired_once:
            self.expired_once = True
            await asyncio.sleep(2.1)
        return result


async def test_expired_activity_input_retries_with_current_fence(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=2)
    probes, temporal = Probes(store), await client()
    async with worker(temporal, ExpiredResponse(store), probes):
        result = await asyncio.wait_for(
            temporal.execute_workflow(
                SegmentScenarioWorkflow.run,
                Scenario(request(account_id, run_id), "timer", 0),
                id=f"expired-segment-{run_id}",
                task_queue=AGENT_TASK_QUEUE,
            ),
            20,
        )
    attempts = [x for x in probes.tokens if x[0] == f"{run_id}:first"]
    assert len(attempts) == 2
    assert result[0] == attempts[1][1] > attempts[0][1]
    assert_no_resources(db, account_id, probes)


async def test_cancel_active_activity_waits_for_resource_cleanup(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=10)
    probes, temporal = Probes(store), await client()
    async with worker(temporal, SegmentActivities(store), probes):
        handle = await temporal.start_workflow(
            SegmentScenarioWorkflow.run,
            Scenario(replace(request(account_id, run_id), objective="block"), "timer", 0),
            id=f"cancel-active-{run_id}",
            task_queue=AGENT_TASK_QUEUE,
        )
        async with asyncio.timeout(10):
            while not probes.workspace_lock.locked():
                await asyncio.sleep(0.05)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), 15)
    assert_no_resources(db, account_id, probes)
    assert len(probes.tokens) == 1
    assert (
        db.execute(
            "SELECT count(*) FROM agent_execution_segments WHERE run_id=%s AND state<>'released'",
            (run_id,),
        ).fetchone()[0]
        == 0
    )
