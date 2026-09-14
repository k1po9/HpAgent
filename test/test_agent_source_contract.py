from dataclasses import asdict, fields, replace

import pytest
from temporalio.converter import DataConverter

from agent_activities.runtime import DurableAgentActivities
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AgentExecutionInput,
    AgentRunInput,
    ChatContext,
    ContextBootstrapInput,
    ExecutionLeaseRef,
    ModelDecisionInput,
    RunContext,
    RunSource,
)
from agent_workflows.react import _validate


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["react", "plan_and_execute"])
async def test_non_chat_run_and_execution_interval_roundtrip(strategy):
    stable = AgentRunInput(
        schema_version=AGENT_SCHEMA_VERSION,
        run_id="run-1",
        account_id="account-1",
        source=RunSource("scheduled_task", "task-1"),
        context=RunContext(context_ref="task-context:1"),
        strategy=strategy,
    )
    assert not {"conversation_id", "session_id", "lease_token", "interaction_profile"} & {
        field.name for field in fields(stable)
    }
    active = AgentExecutionInput(
        **{field.name: getattr(stable, field.name) for field in fields(stable)},
        execution_lease=ExecutionLeaseRef(7),
    )
    _validate(active)
    converter = DataConverter.default
    decoded, = await converter.decode(await converter.encode([active]), [AgentExecutionInput])
    assert decoded == active
    resumed = replace(decoded, execution_lease=ExecutionLeaseRef(8))
    assert resumed.run_id == stable.run_id
    assert resumed.source == stable.source
    assert resumed.context.chat is None
    assert active.execution_lease.fencing_token == 7
    assert resumed.execution_lease.fencing_token == 8


@pytest.mark.asyncio
async def test_non_chat_activity_contract_roundtrip_and_trace():
    request = ModelDecisionInput(
        schema_version=AGENT_SCHEMA_VERSION,
        run_id="run", account_id="account", source=RunSource("file_trigger", "file:1"),
        context=RunContext(context_ref="file-context:1"), strategy="react",
        transcript_id="transcript", transcript_version=3, turn=2,
        operation_id="run:react:turn:2:model", lease_token=9,
    )
    converter = DataConverter.default
    decoded, = await converter.decode(await converter.encode([request]), [ModelDecisionInput])
    assert decoded == request
    assert replace(request, lease_token=10).operation_id == request.operation_id
    correlation = DurableAgentActivities._correlation(request)
    assert correlation["conversation_id"] is None
    assert correlation["session_id"] is None
    assert correlation["surface"] is None


def test_chat_context_is_owned_by_chat_adapter_and_preserves_trace_identity():
    chat = ChatContext("conversation", "session", "message")
    request = ContextBootstrapInput(
        AGENT_SCHEMA_VERSION, "run", "account", RunSource("chat", "conversation"),
        RunContext(chat=chat, surface="qq"), "react", "bootstrap",
    )
    assert request.context.require_chat() == chat
    correlation = DurableAgentActivities._correlation(request)
    assert correlation["surface"] == "qq"
    assert correlation["conversation_id"] == "conversation"
    assert correlation["session_id"] == "session"
    assert "lease_token" not in asdict(request)
    with pytest.raises(ValueError, match="Chat context"):
        RunContext(context_ref="task:1").require_chat()
    with pytest.raises(ValueError, match="Conversation and Session"):
        ChatContext("", "")
    with pytest.raises(ValueError, match="positive"):
        ExecutionLeaseRef(0)
