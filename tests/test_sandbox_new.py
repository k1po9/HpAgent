import pytest
import asyncio
from src.sandbox.sandbox import Sandbox
from src.sandbox.sandbox_manager import SandboxManager
from src.sandbox.tools.factory import ToolFactory
from src.common.errors import ToolNotFoundError, SandboxNotFoundError


@pytest.fixture
def sandbox_manager():
    return SandboxManager()


@pytest.fixture
def sample_tool():
    async def execute(text: str, uppercase: bool = False) -> str:
        if uppercase:
            return text.upper()
        return text
    return ToolFactory.create_tool(
        name="transform_text",
        description="Transform text with optional transformations",
        parameters={"type": "object", "properties": {"text": {"type": "string"}, "uppercase": {"type": "boolean", "default": False}}, "required": ["text"]},
        execute_func=execute,
    )


@pytest.mark.asyncio
async def test_create_sandbox(sandbox_manager):
    sandbox_id = sandbox_manager.create_sandbox()
    assert sandbox_id is not None
    sandbox = sandbox_manager.get_sandbox(sandbox_id)
    assert sandbox.sandbox_id == sandbox_id
    assert sandbox.status == "active"


@pytest.mark.asyncio
async def test_sandbox_with_tools(sandbox_manager, sample_tool):
    sandbox_id = sandbox_manager.create_sandbox(tools=[sample_tool])
    sandbox = sandbox_manager.get_sandbox(sandbox_id)
    tools = await sandbox.list_tools()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "transform_text"


@pytest.mark.asyncio
async def test_execute_tool(sandbox_manager, sample_tool):
    sandbox_id = sandbox_manager.create_sandbox(tools=[sample_tool])
    sandbox = sandbox_manager.get_sandbox(sandbox_id)
    result = await sandbox.execute("transform_text", {"text": "hello"})
    assert result.output == "hello"


@pytest.mark.asyncio
async def test_tool_not_found(sandbox_manager):
    sandbox_id = sandbox_manager.create_sandbox()
    sandbox = sandbox_manager.get_sandbox(sandbox_id)
    with pytest.raises(ToolNotFoundError):
        await sandbox.execute("nonexistent_tool", {})


@pytest.mark.asyncio
async def test_destroy_sandbox(sandbox_manager):
    sandbox_id = sandbox_manager.create_sandbox()
    result = sandbox_manager.destroy_sandbox(sandbox_id)
    assert result is True
    with pytest.raises(SandboxNotFoundError):
        sandbox_manager.get_sandbox(sandbox_id)


@pytest.mark.asyncio
async def test_default_tools():
    tools = ToolFactory.create_default_tools()
    assert len(tools) == 3
    tool_names = [t.name for t in tools]
    assert "calculator" in tool_names
    assert "web_search" in tool_names
    assert "file_read" in tool_names
