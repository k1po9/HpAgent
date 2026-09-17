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
        def get_sandbox_for_session(self, session_id):
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
