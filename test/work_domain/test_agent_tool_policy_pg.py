import json
from dataclasses import replace

import pytest
from support.capability_worker import activities as capability_activities
from test_capability_facts import chat, execution
from test_capability_harness import call, tool_input
from test_delegation import brief, request, setup

from actions.runtime import ActionRuntime
from agent_activities.delegation import DelegationActivities
from agent_activities.runtime import DurableAgentActivities
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    ModelDecisionInput,
    RunContext,
    RunSource,
)
from application.tool_policy import ToolPolicy, tool_policy
from application.work_context import RunExecutionBindings
from brain.contracts import BrainDecision
from resources.run_budget import RunBudgetService
from run_domain.execution_context import fence_scope
from sandbox.sandbox_manager import SandboxManager
from web_domain.run_events import RedisWebRunEventSinkFactory
from workspace.execution import ExecutionResourceService

pytestmark = pytest.mark.postgres


async def test_disabled_capability_blocks_new_invocation_but_preserves_registered_recovery(urls, account_id, owner):
    run = chat(urls, account_id)
    tool = tool_input(urls, account_id, run)
    probe = capability_activities(urls[2])
    allowed = probe.tool_policy
    config = allowed.model_dump()
    config["roles"]["main"]["groups"] = tuple(group for group in config["roles"]["main"]["groups"] if group != "test_echo")
    denied = ToolPolicy.model_validate(config)
    probe.tool_policy = denied
    with execution(urls, account_id, run) as (_, _, token):
        rejected = await probe.prepare(call(tool, token))
        assert "未获准" in rejected.error
        assert owner.execute("SELECT count(*) n FROM capability_invocations WHERE invocation_id=%s", (tool.operation_id,)).fetchone()["n"] == 0
        probe.tool_policy = allowed
        registered = await probe.prepare(call(tool, token))
        assert registered.child is not None
        probe.tool_policy = denied
        replayed = await probe.prepare(call(tool, token))
        assert replayed.child == registered.child


async def test_disabled_delegation_cannot_create_children(urls, account_id, commands, owner, monkeypatch):
    from agent_activities import delegation

    _, run, store, root, _, token = setup(commands, account_id, urls)
    configured = tool_policy().model_dump()
    configured["roles"]["worker"]["groups"] = tuple(group for group in configured["roles"]["worker"]["groups"] if group != "delegation")
    policy = ToolPolicy.model_validate(configured)
    monkeypatch.setattr(delegation, "tool_policy", lambda: policy)
    probe = DelegationActivities(store, RunBudgetService(urls[2]))
    with fence_scope(str(account_id), run["run_id"], token, root):
        tool = replace(request(store, account_id, run, root, [brief()]), lease_token=token)
        prepared = await probe.prepare(tool)
        assert "未获准" in prepared.error
        assert owner.execute("SELECT count(*) n FROM run_executions WHERE parent_execution_id=%s", (root,)).fetchone()["n"] == 0


async def test_model_receives_exact_tools_and_read_only_delegation_choices(urls, account_id, commands, tmp_path):
    _, run, store, root, _, token = setup(commands, account_id, urls)
    observed = {}

    class Brain:
        async def generate_chat_decision(self, *, messages, tools):
            observed["tools"] = {tool["function"]["name"] for tool in tools}
            context = next(message["content"] for message in messages if '"delegatable_tools":' in message.get("content", ""))
            observed["context"] = json.loads(context.splitlines()[-1])
            return BrainDecision("已检查", [], "stop", {})

    manager = SandboxManager(nsjail_enabled=False)
    runtime = DurableAgentActivities(
        store=store, loader=None, brain=Brain(), actions=ActionRuntime(sandbox_manager=manager),
        event_factory=RedisWebRunEventSinkFactory(None), lifecycle=None,
        resource_prep=ExecutionResourceService(urls[2], manager, execution_root=tmp_path / "executions"),
        context_bindings=RunExecutionBindings(),
    )
    try:
        with fence_scope(str(account_id), run["run_id"], token, root):
            store.begin_operation("context:" + root, run["run_id"], "context")
            store.create_transcript(
                transcript_id=root, run_id=run["run_id"], account_id=str(account_id),
                execution_id=root, conversation_id=None, messages=[{"role": "user", "content": "检查工具"}],
                operation_id="context:" + root,
            )
            result = await runtime.model_decision(ModelDecisionInput(
                AGENT_SCHEMA_VERSION, run["run_id"], str(account_id), RunSource("work", run["work_id"]), RunContext(),
                "react", root, 1, 0, root + ":model", token, execution_id=root,
            ))
        assert result.decision_type == "final"
        assert set(observed["context"]["tools"]) == observed["tools"]
        assert observed["context"]["role"] == "worker"
        assert "delegate_work" in observed["tools"]
        assert "accept_work" not in observed["tools"]
        assert set(observed["context"]["delegatable_tools"]) == {"fs_read", "Glob", "Grep"}
    finally:
        manager.close()
