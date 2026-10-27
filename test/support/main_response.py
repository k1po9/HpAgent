"""Commit a genuine Main response receipt with a deterministic in-process Brain."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from actions.contracts import ActionRequest
from agent_activities.runtime import DurableAgentActivities
from agent_activities.store import AgentDataStore
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    ModelDecisionInput,
    RunContext,
    RunSource,
)
from agent_workflows.lifecycle_contracts import SegmentInput
from brain.contracts import BrainDecision
from conversation_domain.execution_bindings import ChatExecutionBindings
from resources.run_budget import RunBudgetService
from run_domain.execution_context import fence_scope
from web_domain.lifecycle import WebRunLifecycleService


async def respond_directly_receipt(database, run_id, account_id, answer):
    store = AgentDataStore(database)
    identity = store.run_identity(str(run_id))
    segment = SegmentInput(2, str(run_id), str(account_id), str(uuid4()),
                           execution_id=str(identity['execution_id']))
    token = store.acquire_segment(segment)
    try:
        with fence_scope(str(account_id), str(run_id), token, str(identity['execution_id'])):
            tid, operation = f'test-response:{run_id}', f'test-context:{run_id}'
            store.begin_operation(operation, str(run_id), 'context')
            store.create_transcript(
                transcript_id=tid, run_id=str(run_id), account_id=str(account_id),
                execution_id=segment.execution_id, conversation_id=str(identity['conversation_id']),
                session_id=identity['session_id'], messages=[{'role': 'user', 'content': 'Synthetic race fixture'}],
                operation_id=operation)
            decision = BrainDecision('', [ActionRequest('answer', 'respond_directly', {'answer': answer})], 'tool_calls', {})
            events = SimpleNamespace(progress=AsyncMock(), trace_start=AsyncMock(), trace_end=AsyncMock(), close=AsyncMock())
            runtime = DurableAgentActivities(
                context_bindings=ChatExecutionBindings(), store=store, loader=None,
                brain=SimpleNamespace(generate_chat_decision=AsyncMock(return_value=decision)),
                actions=SimpleNamespace(reset_execution=Mock(), clear_execution=Mock(),
                                        select_tools=AsyncMock(return_value=[]), side_effect_class=Mock(return_value='read_only')),
                event_factory=SimpleNamespace(for_run=lambda _: events), resource_prep=None,
                lifecycle=WebRunLifecycleService(database), run_budget=RunBudgetService(database))

            @asynccontextmanager
            async def workspace(*args):
                yield

            runtime._workspace = workspace
            result = await runtime.model_decision(ModelDecisionInput(
                AGENT_SCHEMA_VERSION, str(run_id), str(account_id), RunSource('chat', str(uuid4())),
                RunContext(), 'react', tid, 1, 1, f'test-decision:{run_id}', token,
                execution_id=segment.execution_id))
            assert result.decision_type == 'final'
            return result.decision_ref
    finally:
        store.release_segment(segment)
