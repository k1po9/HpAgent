from __future__ import annotations

import pytest

from actions.contracts import ActionRequest, ActionResult
from actions.runtime import ActionRuntime


def test_action_result_preserves_explicit_failure_semantics() -> None:
    request = ActionRequest("call-1", "read_file", {})
    result = ActionResult.from_runtime_result(
        request,
        {"success": False, "output": None, "error": "mock tool failed"},
    )

    assert result.success is False
    assert result.failed is True
    assert result.error == "mock tool failed"
    assert result.display_result is None


def test_action_result_legacy_error_still_counts_as_failure() -> None:
    result = ActionResult(ActionRequest("call-1", "legacy", {}), error="failed")
    assert result.success is None
    assert result.failed is True


@pytest.mark.asyncio
async def test_action_runtime_exception_returns_uniform_failure() -> None:
    class Sandbox:
        async def execute(self, tool_name, arguments):
            raise RuntimeError("adapter exploded")

    class Sandboxes:
        def get_sandbox_for_execution(self, session_id):
            return Sandbox()

    result = await ActionRuntime(sandbox_manager=Sandboxes())._execute(
        tool_name="broken", arguments={}, resource_key="session", execution_id="run"
    )

    assert result == {
        "success": False,
        "output": None,
        "error": "adapter exploded",
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_action_runtime_rejects_empty_execution_identity() -> None:
    runtime = ActionRuntime()
    with pytest.raises(ValueError, match="resource_key and execution_id are required"):
        await runtime.select_tools(
            user_content="hello", resource_key="session", execution_id=""
        )


async def test_selection_is_fresh_for_context_hints_and_permissions():
    from types import SimpleNamespace

    from sandbox.tools.routing.models import ToolSelectionResult

    calls = []

    class Sandbox:
        async def select_tools(self, query, limit):
            calls.append(query)
            return ToolSelectionResult(({'name': str(len(calls))},), {'queries': [query]})

    runtime = ActionRuntime(sandbox_manager=SimpleNamespace(get_sandbox_for_execution=lambda _: Sandbox()))
    results = [await runtime.select_tools(user_content='same', group_context_text=context,
                                         resource_key='r', execution_id='e')
               for context in ('first', 'second', 'second')]
    assert [r.tool_schemas[0]['name'] for r in results] == ['1', '2', '3']
    assert calls == ['same\n--- 群聊 ---\nfirst', 'same\n--- 群聊 ---\nsecond', 'same\n--- 群聊 ---\nsecond']


async def test_missing_sandbox_is_failed_selection_not_empty_tools():
    result = await ActionRuntime().select_tools(resource_key='r', execution_id='e')
    assert result.status == 'failed' and not result.tool_schemas
    assert result.error.code == 'tool_retrieval_sandbox_unavailable'
