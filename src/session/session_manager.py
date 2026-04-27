"""
会话管理器（实现 ISession 接口）
负责会话生命周期管理、事件校验与业务逻辑，持久化委托给仓库层
"""
from typing import Dict, List, Optional, Any
import time
import uuid

from common.types import Event, SessionMetadata, ChannelType, EventType
from common.interfaces import ISession
from common.errors import SessionNotFoundError, ValidationError
from session.models import Session, EventRecord, SessionStatus
from session.repositories import FileSessionRepository, FileEventRepository


class SessionManager(ISession):
    """实现 ISession 接口的会话管理器"""

    def __init__(self, storage_path: Optional[str] = None):
        """
        初始化会话管理器
        :param storage_path: 持久化存储路径，为 None 时仅内存存储
        """
        self._session_repo = FileSessionRepository(storage_path)
        self._event_repo = FileEventRepository(storage_path)

    # ------------------------------------------------------------------
    # ISession 接口实现
    # ------------------------------------------------------------------

    async def create_session(self, metadata: SessionMetadata) -> str:
        """创建新会话"""
        if self._session_repo.get(metadata.session_id):
            raise ValidationError("session_id", "Session already exists")

        session = Session(
            session_id=metadata.session_id,
            creator_id=metadata.creator_id,
            channel_type=(
                metadata.channel_type.value
                if hasattr(metadata.channel_type, "value")
                else str(metadata.channel_type)
            ),
            tags=metadata.tags,
            status=SessionStatus.ACTIVE,
            created_at=metadata.created_at,
        )
        self._session_repo.save(session)
        return session.session_id

    async def emit_event(self, event: Event) -> str:
        """追加事件到会话日志"""
        session = self._session_repo.get(event.session_id)
        if not session:
            raise SessionNotFoundError(event.session_id)
        if session.status != SessionStatus.ACTIVE:
            raise ValidationError(
                "session_status",
                f"Cannot emit event to {session.status.value} session",
            )

        record = EventRecord(
            event_id=event.event_id,
            session_id=event.session_id,
            event_index=-1,  # 仓库会填充实际索引
            timestamp=event.timestamp,
            event_type=(
                event.event_type.value
                if hasattr(event.event_type, "value")
                else event.event_type
            ),
            content=event.content,
            metadata=event.metadata,
        )
        self._event_repo.append_event(event.session_id, record)

        # 更新会话最后修改时间
        session.updated_at = event.timestamp
        self._session_repo.save(session)

        return event.event_id

    async def get_events(
        self,
        session_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[Event]:
        """获取会话事件列表"""
        if not self._session_repo.get(session_id):
            raise SessionNotFoundError(session_id)

        records = self._event_repo.get_events(
            session_id, offset, limit, event_types
        )
        return [
            Event(
                event_id=r.event_id,
                session_id=r.session_id,
                timestamp=r.timestamp,
                event_type=EventType(r.event_type),
                content=r.content,
                metadata=r.metadata,
            )
            for r in records
        ]

    async def rewind_session(
        self, session_id: str, target_event_id: str
    ) -> Dict[str, Any]:
        """回滚会话到指定事件"""
        session = self._session_repo.get(session_id)
        if not session:
            raise SessionNotFoundError(session_id)

        events = self._event_repo.get_events(session_id)
        target_index = None
        for i, e in enumerate(events):
            if e.event_id == target_event_id:
                target_index = i
                break

        if target_index is None:
            raise ValidationError("target_event_id", "Event not found")

        removed_count = self._event_repo.truncate_events(
            session_id, target_index + 1
        )
        session.updated_at = events[target_index].timestamp
        self._session_repo.save(session)

        return {
            "session_id": session_id,
            "rewound_to_event_id": target_event_id,
            "removed_events_count": removed_count,
        }

    async def archive_session(self, session_id: str) -> bool:
        """归档会话"""
        session = self._session_repo.get(session_id)
        if not session:
            raise SessionNotFoundError(session_id)
        session.status = SessionStatus.ARCHIVED
        self._session_repo.save(session)
        return True

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[SessionMetadata]:
        """列出会话"""
        sessions = self._session_repo.list_all()
        if status:
            sessions = [
                s for s in sessions if s.status.value == status
            ]
        if tags:
            sessions = [
                s for s in sessions if any(tag in s.tags for tag in tags)
            ]

        sessions.sort(key=lambda s: s.created_at, reverse=True)
        paged = sessions[offset : offset + limit]

        return [
            SessionMetadata(
                session_id=s.session_id,
                creator_id=s.creator_id,
                channel_type=(
                    ChannelType(s.channel_type)
                    if s.channel_type
                    else ChannelType.CONSOLE
                ),
                tags=s.tags,
                created_at=s.created_at,
                status=s.status.value,
            )
            for s in paged
        ]

    # ------------------------------------------------------------------
    # 便利方法
    # ------------------------------------------------------------------

    async def create_session_with_id(
        self,
        creator_id: str = "",
        channel_type: ChannelType = ChannelType.CONSOLE,
        tags: Optional[list] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """创建新会话（自动生成 session_id 并发送 SESSION_START 事件）"""
        session_id = str(uuid.uuid4())
        session_metadata = SessionMetadata(
            session_id=session_id,
            creator_id=creator_id,
            channel_type=channel_type,
            tags=tags or [],
            created_at=time.time(),
            status="active",
        )
        session_metadata.metadata = metadata or {}
        await self.create_session(session_metadata)

        start_event = Event(
            session_id=session_id,
            event_type=EventType.SESSION_START,
            content={
                "creator_id": creator_id,
                "channel_type": (
                    channel_type.value
                    if hasattr(channel_type, "value")
                    else str(channel_type)
                ),
            },
            metadata={"initial": True},
        )
        await self.emit_event(start_event)
        return session_id

    async def append_user_message(
        self,
        session_id: str,
        content: str,
        sender_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        event = Event(
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            content={"text": content, "sender_id": sender_id},
            metadata=metadata or {},
        )
        return await self.emit_event(event)

    async def append_model_message(
        self,
        session_id: str,
        content: str,
        tool_calls: Optional[list] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        event = Event(
            session_id=session_id,
            event_type=EventType.MODEL_MESSAGE,
            content={"text": content, "tool_calls": tool_calls or []},
            metadata=metadata or {},
        )
        return await self.emit_event(event)

    async def append_tool_call(
        self,
        session_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_call_id: str = "",
    ) -> str:
        event = Event(
            session_id=session_id,
            event_type=EventType.TOOL_CALL,
            content={
                "tool_name": tool_name,
                "arguments": arguments,
                "tool_call_id": tool_call_id or str(uuid.uuid4()),
            },
        )
        return await self.emit_event(event)

    async def append_tool_result(
        self,
        session_id: str,
        tool_call_id: str,
        result: Any,
        error: Optional[str] = None,
    ) -> str:
        event = Event(
            session_id=session_id,
            event_type=EventType.TOOL_RESULT,
            content={
                "tool_call_id": tool_call_id,
                "result": result,
                "error": error,
                "status": "error" if error else "success",
            },
        )
        return await self.emit_event(event)

    async def append_error(
        self,
        session_id: str,
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        event = Event(
            session_id=session_id,
            event_type=EventType.ERROR,
            content={
                "error_type": error_type,
                "message": message,
                "details": details or {},
            },
        )
        return await self.emit_event(event)

    async def complete_session(self, session_id: str) -> str:
        event = Event(
            session_id=session_id, event_type=EventType.SESSION_COMPLETE, content={}
        )
        event_id = await self.emit_event(event)
        await self.archive_session(session_id)
        return event_id

    async def get_full_history(self, session_id: str) -> list[Event]:
        return await self.get_events(session_id)

    async def rewind_to_event(
        self, session_id: str, event_id: str
    ) -> Dict[str, Any]:
        return await self.rewind_session(session_id, event_id)

    def get_session_info(self, session_id: str) -> Optional[Session]:
        return self._session_repo.get(session_id)

    async def list_active_sessions(
        self, limit: int = 50, offset: int = 0
    ) -> list[SessionMetadata]:
        return await self.list_sessions(
            limit=limit, offset=offset, status="active"
        )