from typing import Dict, Any, Optional, List, Callable, Awaitable
from threading import RLock
import time
from common.types import Event, EventType, ModelResponse, ToolCall, ToolResult, StopReason
from common.interfaces import ISession, IHarness
from common.errors import SessionNotFoundError
from harness.context_builder import HarnessContextBuilder
from resources.resource_pool import ResourcePool
from resources.credentials import ModelEndpoint
from sandbox.sandbox_manager import SandboxManager


class Harness(IHarness):
    def __init__(self, session_store: ISession, resource_pool: ResourcePool, sandbox_manager: SandboxManager, system_prompt: str = "You are a helpful AI assistant.", max_turns: int = 20, default_model: str = "main"):
        self._session_store = session_store
        self._resource_pool = resource_pool
        self._sandbox_manager = sandbox_manager
        self._context_builder = HarnessContextBuilder(system_prompt)
        self._max_turns = max_turns
        self._system_prompt = system_prompt
        self._lock = RLock()

    async def wake(self, session_id: str) -> ModelResponse:
        with self._lock:
            events = await self._session_store.get_events(session_id)
            if not events:
                return ModelResponse(content="Session has no events.", stop_reason=StopReason.END_TURN)
            
            tools = await self._get_available_tools()
            context = self._context_builder.build(events, self._max_turns)
            model_response = await self._call_model_internal(context, tools)
            response_event = Event(session_id=session_id, event_type=EventType.MODEL_MESSAGE, content={"text": model_response.content, "tool_calls": [tc.to_dict() for tc in model_response.tool_calls] if model_response.tool_calls else []}, metadata={"stop_reason": model_response.stop_reason.value, "usage": model_response.usage or {}})
            await self._session_store.emit_event(response_event)
            
            return model_response

    async def build_context(self, events: List[Event]) -> List[Dict[str, Any]]:
        return self._context_builder.build(events, self._max_turns)

    async def call_model(self, context: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> ModelResponse:
        return await self._call_model_internal(context, tools or [])

    async def route_tool_call(self, tool_call: ToolCall) -> ToolResult:
        sandbox_id = self._get_sandbox_for_tool(tool_call.name)
        if not sandbox_id:
            return ToolResult(tool_call_id=tool_call.id, status="error", content="", error=f"No sandbox available for tool: {tool_call.name}")
        try:
            sandbox = self._sandbox_manager.get_sandbox(sandbox_id)
            result = await sandbox.execute(tool_call.name, tool_call.arguments)
            if hasattr(result, 'to_dict'):
                result_dict = result.to_dict()
            else:
                result_dict = {"success": True, "output": result, "error": None}
            return ToolResult(tool_call_id=tool_call.id, status="error" if result_dict.get("error") else "success", content=result_dict.get("output", ""), error=result_dict.get("error"))
        except Exception as e:
            return ToolResult(tool_call_id=tool_call.id, status="error", content="", error=str(e))

    async def handle_error(self, error: Dict[str, Any]) -> Dict[str, Any]:
        error_type = error.get("type", "unknown")
        error_msg = error.get("message", "Unknown error")
        session_id = error.get("session_id")
        if session_id:
            error_event = Event(session_id=session_id, event_type=EventType.ERROR, content={"error_type": error_type, "message": error_msg, "details": error.get("details", {})})
            await self._session_store.emit_event(error_event)
        recovery_strategy = "retry" if self._is_recoverable(error_type) else "abort"
        return {"strategy": recovery_strategy, "error_type": error_type, "message": error_msg}

    async def _get_available_tools(self) -> List[Dict[str, Any]]:
        tools = []
        for sandbox_info in self._sandbox_manager.list_sandboxes():
            if sandbox_info["status"] != "active":
                continue
            sandbox = self._sandbox_manager.get_sandbox(sandbox_info["sandbox_id"])
            tools.extend(await sandbox.list_tools())
        return tools

    async def _call_model_internal(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelResponse:
        return await self._resource_pool.generate(
            messages=messages,
            tools=tools if tools else None,
            stream=False,
        )

    def _get_sandbox_for_tool(self, tool_name: str) -> Optional[str]:
        for sandbox_info in self._sandbox_manager.list_sandboxes():
            if sandbox_info["status"] != "active":
                continue
            sandbox = self._sandbox_manager.get_sandbox(sandbox_info["sandbox_id"])
            if sandbox.has_tool(tool_name):
                return sandbox_info["sandbox_id"]
        return None

    def _is_recoverable(self, error_type: str) -> bool:
        recoverable_types = {"network_error", "timeout", "model_api_error", "tool_execution_failed"}
        return error_type in recoverable_types
