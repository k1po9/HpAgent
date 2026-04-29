"""
Temporal Activities — the Harness's decomposed brain operations.

Each Activity wraps a single non-deterministic operation (API call, tool execution, I/O).
They are stateless: dependencies are injected at Worker startup via inject().
"""
from typing import List, Dict, Any, Optional

from temporalio import activity

from common.types import Event, ChannelType, EventType, UnifiedMessage

# ---- Module-level singletons (injected at Worker startup) ----
_context_builder = None
_resource_pool = None
_sandbox_manager = None
_channel = None


def inject(
    context_builder=None,
    resource_pool=None,
    sandbox_manager=None,
    channel=None,
) -> None:
    """Inject shared dependencies before Worker starts. Called once at boot."""
    global _context_builder, _resource_pool, _sandbox_manager, _channel
    _context_builder = context_builder
    _resource_pool = resource_pool
    _sandbox_manager = sandbox_manager
    _channel = channel


@activity.defn
async def build_context_activity(
    events: List[Dict[str, Any]],
    channel_type: str = "",
) -> List[Dict[str, Any]]:
    """Assemble LLM messages list from event history and channel identity."""
    event_objs = [Event.from_dict(e) if isinstance(e, dict) else e for e in events]
    ch_type = ChannelType(channel_type) if channel_type else None
    return _context_builder.build(events=event_objs, channel_type=ch_type)


@activity.defn
async def get_available_tools_activity() -> List[Dict[str, Any]]:
    """Collect tool definitions across all active sandboxes."""
    tools = []
    for sandbox_info in _sandbox_manager.list_sandboxes():
        if sandbox_info["status"] != "active":
            continue
        sandbox = _sandbox_manager.get_sandbox(sandbox_info["sandbox_id"])
        tools.extend(await sandbox.list_tools())
    return tools


@activity.defn
async def call_model_activity(
    context: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Invoke the LLM via ResourcePool with fallback chain."""
    response = await _resource_pool.generate(
        messages=context,
        tools=tools if tools else None,
        stream=False,
    )
    return {
        "content": response.content,
        "tool_calls": [
            tc.to_dict() for tc in (response.tool_calls or [])
        ],
        "stop_reason": response.stop_reason.value,
        "usage": response.usage or {},
    }


@activity.defn
async def execute_tool_activity(
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """Route a tool call to the correct sandbox and return the result."""
    for sandbox_info in _sandbox_manager.list_sandboxes():
        if sandbox_info["status"] != "active":
            continue
        sandbox = _sandbox_manager.get_sandbox(sandbox_info["sandbox_id"])
        if sandbox.has_tool(tool_name):
            result = await sandbox.execute(tool_name, arguments)
            if hasattr(result, "to_dict"):
                return result.to_dict()
            return {"output": str(result), "error": None}
    return {"output": None, "error": f"Tool '{tool_name}' not found"}


@activity.defn
async def send_response_activity(
    content: str,
    user_message: Dict[str, Any],
) -> bool:
    """Deliver the final assistant response through the channel."""
    if _channel is None:
        return False

    ch_type = user_message.get("channel_type", "console")
    if isinstance(ch_type, str):
        try:
            ch_type = ChannelType(ch_type)
        except ValueError:
            ch_type = ChannelType.CONSOLE

    msg = UnifiedMessage(
        session_id=user_message.get("session_id", ""),
        sender_id=user_message.get("sender_id", ""),
        channel_type=ch_type,
        content=content,
        metadata=user_message.get("metadata", {}),
    )
    return await _channel.send_message(msg)
