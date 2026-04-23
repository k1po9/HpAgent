from typing import Dict, Any, Optional, List
from threading import RLock
import uuid
import time
from ..common.types import UnifiedMessage, Event, EventType
from ..common.interfaces import IOrchestration, IHarness, ISession, ISandbox
from ..sandbox.sandbox_manager import SandboxManager
from ..sandbox.tools.factory import ToolFactory
from ..session.session_manager import SessionManager
from ..resources.resource_pool import ResourcePool
from ..harness.harness import Harness


class Orchestrator(IOrchestration):
    """
    Orchestrator 是有状态的，负责维护每个请求的任务生命周期。
    它不直接执行模型调用，而是把实际工作委托给 Harness，自己专注于任务状态机和异常恢复。
    """
    def __init__(self, session_manager: SessionManager, harness: Harness, sandbox_manager: SandboxManager, resource_pool: ResourcePool):
        self._session_manager = session_manager      # 会话存储/管理
        self._harness = harness                      # 模型调用 + 工具路由
        self._sandbox_manager = sandbox_manager      # 工具执行环境
        self._resource_pool = resource_pool          # 访问外部资源
        self._active_tasks: Dict[str, Dict] = {}      # 任务调度核心
        self._harness_instances: Dict[str, str] = {}  # 暂未启用
        self._lock = RLock()
        self._default_tools = ToolFactory.create_default_tools()

    # ======================== harness ========================
    async def allocate_harness(self, session_id: str, model_requirements: Dict[str, Any]) -> str:
        with self._lock:
            harness_id = str(uuid.uuid4())
            self._harness_instances[harness_id] = session_id
            return harness_id


    # ======================== 执行沙箱 ========================
    async def provision_sandbox(self, tools: List[str], resources: Dict[str, Any]) -> str:
        sandbox_tools = self._default_tools.copy()
        sandbox_id = self._sandbox_manager.create_sandbox(tools=sandbox_tools, resources=resources)
        return sandbox_id

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        return self._sandbox_manager.destroy_sandbox(sandbox_id)


    # ======================== 任务状态 ========================
    async def receive_request(self, message: UnifiedMessage) -> Dict[str, Any]:
        with self._lock:
            session_id = await self._get_or_create_session(message)
            event = Event(
                session_id=session_id, 
                event_type=EventType.USER_MESSAGE, 
                content={
                    "message_id": message.message_id, 
                    "sender_id": message.sender_id, 
                    "channel_type": message.channel_type.value if hasattr(message.channel_type, 'value') else str(message.channel_type), 
                    "content": message.content, 
                    "media_urls": message.media_urls}, 
                metadata=message.metadata
            )
            await self._session_manager.emit_event(event)
            task_id = str(uuid.uuid4())
            self._active_tasks[task_id] = {"session_id": session_id, "status": "pending", "created_at": time.time()}
            return {"task_id": task_id, "session_id": session_id, "status": "received"}

    async def retry_task(self, session_id: str, failed_event_id: str) -> str:
        with self._lock:
            new_task_id = str(uuid.uuid4())
            self._active_tasks[new_task_id] = {
                "session_id": session_id,
                "status": "retry",
                "original_event_id": failed_event_id,
                "created_at": time.time()
            }
            events = await self._session_manager._event_store.get_events(session_id)
            rewind_event = next((e for e in reversed(events) if e.event_id == failed_event_id), None)
            if rewind_event:
                await self._session_manager._event_store.rewind_session(session_id, rewind_event.event_id)
            return new_task_id

    async def cancel_task(self, session_id: str) -> bool:
        with self._lock:
            for task_id, task in list(self._active_tasks.items()):
                if task["session_id"] == session_id:
                    task["status"] = "cancelled"
            return True

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._active_tasks.get(task_id)

    def get_active_tasks_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._active_tasks.values() if t["status"] == "pending")

    # ======================== Session ========================
    async def process_session(self, session_id: str) -> Dict[str, Any]:
        events = await self._session_manager.get_events(session_id)
        if not events:
            return {"status": "no_events"}
        harness_response = await self._harness.wake(session_id)
        if harness_response.tool_calls:
            for tool_call in harness_response.tool_calls:
                await self._harness.route_tool_call(tool_call)
        return {
            "status": "completed",
            "content": harness_response.content,
            "tool_calls_count": len(harness_response.tool_calls) if harness_response.tool_calls else 0
        }

    async def _get_or_create_session(self, message: UnifiedMessage) -> str:
        sessions = await self._session_manager.list_active_sessions(limit=100)
        for session in sessions:
            if session.creator_id == message.sender_id:
                return session.session_id
        return await self._session_manager.create_session_with_id(creator_id=message.sender_id, channel_type=message.channel_type)



    def get_sandbox_count(self) -> int:
        return self._sandbox_manager.get_sandbox_count()
