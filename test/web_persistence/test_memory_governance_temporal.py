"""Real Temporal resumes a sent model call after withdrawal; tool and fallback refuse."""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from agent_activities.store import AgentDataStore
from conversation_domain.commands import CommandService
from persistence.memory_context import MemoryContextRevoked, bind_context
from persistence.uow import UnitOfWork
from resources.model_budget_coordinator import ModelBudgetCoordinator
from resources.model_input_snapshot import SnapshotRepository
from run_domain.execution_context import fence_scope

from .test_memory_governance import command, verified
from .test_model_input_snapshot import _prepared
from .test_phase_a_invariants import _conversation_and_run

pytestmark = [pytest.mark.postgres, pytest.mark.temporal]
STATE: SimpleNamespace | None = None


@activity.defn
async def memory_late_model() -> str:
    assert STATE is not None
    s = STATE
    with fence_scope(str(s.account), str(s.run), s.token, str(s.execution)):
        snapshot = SnapshotRepository(s.database)
        ref = await asyncio.to_thread(snapshot.create, account_id=s.account, run_id=s.run,
            model_call_id=uuid4(), operation_id='temporal-memory-model', execution_attempt=1,
            call_ordinal=1, fallback_attempt=1, phase='decision', entitlement_version=1, prepared=_prepared())
        await asyncio.to_thread(ModelBudgetCoordinator(s.database).reserve,
            s.account, s.run, 'temporal-memory-model', 8,
            {'model_input_tokens': 3, 'model_output_tokens': 5, 'model_total_tokens': 8, 'model_calls': 1},
            snapshot_id=ref.snapshot_id)
        await asyncio.to_thread(snapshot.mark_dispatched, s.account, ref.snapshot_id)
        s.sent.set()
        await s.response.wait()
        try:
            await asyncio.to_thread(s.store.begin_tool_operation, 'temporal-late-tool', str(s.run))
        except MemoryContextRevoked as exc:
            raise ApplicationError(exc.safe_message, type=exc.code, non_retryable=True) from exc
    return 'tool allowed'


@activity.defn
async def memory_final_fallback() -> str:
    assert STATE is not None
    s = STATE
    try:
        await asyncio.to_thread(CommandService(s.database).complete_run, s.account, s.run, 'old fallback')
    except MemoryContextRevoked as exc:
        raise ApplicationError(exc.safe_message, type=exc.code, non_retryable=True) from exc
    return 'final allowed'


@workflow.defn
class MemoryRevocationWorkflow:
    @workflow.run
    async def run(self) -> str:
        try:
            return await workflow.execute_activity(memory_late_model, start_to_close_timeout=timedelta(seconds=30))
        except ActivityError:
            return await workflow.execute_activity(memory_final_fallback, start_to_close_timeout=timedelta(seconds=10))


async def test_temporal_late_model_cannot_tool_or_fallback(db, database_url, worker_database_url, account_id):
    global STATE
    _, _, remote, source, _ = await verified(db, database_url, worker_database_url, account_id)
    _, _, run = _conversation_and_run(database_url, account_id)
    db.execute("INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) "
               "VALUES (%s,'standard','none') ON CONFLICT DO NOTHING", (account_id,))
    CommandService(worker_database_url).start_run(account_id, run)
    execution = db.execute('SELECT execution_id FROM run_executions WHERE run_id=%s', (run,)).fetchone()[0]
    with UnitOfWork(worker_database_url) as uow:
        bind_context(uow, account_id, execution, [['scope:private']], {'source'}, 0)
    store = AgentDataStore(worker_database_url)
    lease = store.acquire_lease(str(account_id), str(run), str(execution))
    STATE = SimpleNamespace(account=account_id, run=run, execution=execution, database=worker_database_url,
                            token=lease.fencing_token, store=store, sent=asyncio.Event(), response=asyncio.Event())
    client = await Client.connect(os.environ['TEMPORAL_HOST'], namespace=os.environ['TEMPORAL_TEST_NAMESPACE'])
    queue = 'g3-' + str(run)
    async with Worker(client, task_queue=queue, workflows=[MemoryRevocationWorkflow],
                      activities=[memory_late_model, memory_final_fallback],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        handle = await client.start_workflow(MemoryRevocationWorkflow.run, id=queue, task_queue=queue)
        await asyncio.wait_for(STATE.sent.wait(), 10)
        command(database_url, account_id, source, 'invalidate', remote.fact_id)
        STATE.response.set()
        with pytest.raises(WorkflowFailureError) as failure:
            await handle.result()
        assert failure.value.cause.cause.type == 'memory_context_revoked'
    assert db.execute("SELECT count(*) FROM execution_operations WHERE operation_id='temporal-late-tool'").fetchone()[0] == 0
    assert db.execute('SELECT status,content FROM messages WHERE produced_by_run_id=%s', (run,)).fetchone() == ('pending', None)
    STATE = None
