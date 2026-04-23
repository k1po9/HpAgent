以下是一份可直接交付给 Coding Agent 执行的明确指令文档，包含**文件操作清单**、**需要替换的代码内容**、**修改影响范围**和**验证步骤**。文档采用“直接替换、不保留旧实现”的策略。

---

# Session 模块重构执行指令（Coding Agent 专用）

## 📌 任务目标

将 `src/session/` 模块从 **单体 EventStore（同时管理会话状态与持久化）** 重构为 **分层架构**，实现 `ISession` 接口与持久化层的解耦，同时 **完全保持对外接口不变**，确保其他模块（Harness、Orchestration 等）零改动。

## ⚠️ 约束条件

- **直接替换**：删除原有 `event_store.py` 及相关旧实现，不保留向后兼容适配器。
- **接口稳定**：对外 `ISession` 接口方法签名、行为必须与原 `EventStore` 完全一致。
- **测试通过**：重构后必须能通过 `tests/new_arch/test_session.py` 中的所有测试（7个测试）。
- **目录结构**：基于给定的 `src/` 目录结构操作。

---

## 📁 文件操作清单

### 1. 需要删除的文件

```
src/session/event_store.py          # 旧 EventStore 实现（混合职责）
```

### 2. 需要新建的文件

```
src/session/repositories.py         # 新增：持久化仓库层（SessionRepository + EventRepository）
src/session/interfaces.py           # 新增：内部接口定义（IEventRepository, ISessionRepository）【可选，也可直接写在 repositories.py 内】
```

### 3. 需要修改的文件

```
src/session/session_manager.py      # 修改：由薄封装变为 ISession 的唯一实现，内聚业务逻辑
src/session/__init__.py             # 修改：导出新的 SessionManager 及必要的类型
```

### 4. 需要检查但理论上无需修改的文件

以下文件依赖 `ISession` 接口，由于接口不变，应无需改动，但需在修改后验证：

```
src/orchestration/orchestrator.py   # 依赖 ISession
src/harness/harness.py              # 依赖 ISession
src/migration/legacy_converter.py   # 依赖 ISession
tests/new_arch/test_session.py      # 测试文件
```

---

## 🧩 详细代码修改要求

### 一、新建 `src/session/repositories.py`

该文件包含两个基于文件存储的仓库实现，负责 JSON 文件的读写与内存缓存。

```python
"""
Session 层持久化仓库实现
采用文件存储（JSON），支持内存缓存与线程安全
"""
import json
from pathlib import Path
from typing import Dict, List, Optional
from threading import RLock

from .models import Session, EventRecord


class FileSessionRepository:
    """会话元数据仓库"""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path
        self._sessions: Dict[str, Session] = {}
        self._lock = RLock()
        if storage_path:
            self._load()

    def _load(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path) / "sessions.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    session = Session.from_dict(item)
                    self._sessions[session.session_id] = session
        except Exception:
            pass

    def _save(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path)
        path.mkdir(parents=True, exist_ok=True)
        sessions_file = path / "sessions.json"
        with open(sessions_file, "w", encoding="utf-8") as f:
            json.dump(
                [s.to_dict() for s in self._sessions.values()],
                f,
                ensure_ascii=False,
                indent=2,
            )

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            self._save()

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_all(self) -> List[Session]:
        with self._lock:
            return list(self._sessions.values())


class FileEventRepository:
    """事件日志仓库"""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path
        self._events: Dict[str, List[EventRecord]] = {}
        self._counters: Dict[str, int] = {}
        self._lock = RLock()
        if storage_path:
            self._load()

    def _load(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path) / "events.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for session_id, events_list in data.items():
                    records = [EventRecord.from_dict(e) for e in events_list]
                    self._events[session_id] = records
                    if records:
                        max_index = max(e.event_index for e in records)
                        self._counters[session_id] = max_index + 1
        except Exception:
            pass

    def _save(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path)
        path.mkdir(parents=True, exist_ok=True)
        events_file = path / "events.json"
        with open(events_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    sid: [e.to_dict() for e in events]
                    for sid, events in self._events.items()
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def append_event(self, session_id: str, event: EventRecord) -> int:
        with self._lock:
            if session_id not in self._events:
                self._events[session_id] = []
                self._counters[session_id] = 0
            index = self._counters[session_id]
            event.event_index = index
            self._events[session_id].append(event)
            self._counters[session_id] = index + 1
            self._save()
            return index

    def get_events(
        self,
        session_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[EventRecord]:
        with self._lock:
            events = self._events.get(session_id, [])
            if event_types:
                events = [e for e in events if e.event_type in event_types]
            events = events[offset:]
            if limit is not None:
                events = events[:limit]
            return events

    def truncate_events(self, session_id: str, target_index: int) -> int:
        with self._lock:
            events = self._events.get(session_id, [])
            original_len = len(events)
            self._events[session_id] = events[:target_index]
            self._counters[session_id] = target_index
            self._save()
            return original_len - target_index

    def get_event_count(self, session_id: str) -> int:
        with self._lock:
            return self._counters.get(session_id, 0)
```

---

### 二、修改 `src/session/session_manager.py`

将原文件内容**完全替换**为以下实现。新实现直接作为 `ISession` 的唯一实现者。

```python
"""
会话管理器（实现 ISession 接口）
负责会话生命周期管理、事件校验与业务逻辑，持久化委托给仓库层
"""
from typing import Dict, List, Optional, Any
import time
import uuid

from ..common.types import Event, SessionMetadata, ChannelType, EventType
from ..common.interfaces import ISession
from ..common.errors import SessionNotFoundError, ValidationError
from .models import Session, EventRecord, SessionStatus
from .repositories import FileSessionRepository, FileEventRepository


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
    # ISession 接口实现（与原 EventStore 方法签名、行为完全一致）
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
    # 便利方法（与原 SessionManager 保持一致）
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
```

---

### 三、修改 `src/session/__init__.py`

更新导出，移除对 `EventStore` 的引用，暴露新的 `SessionManager`。

```python
"""
Session 模块 - 数据层实现

提供：
- SessionManager: ISession 接口实现
- Session, EventRecord: 数据模型
- FileSessionRepository, FileEventRepository: 持久化仓库
"""
from .session_manager import SessionManager
from .models import Session, EventRecord, SessionStatus
from .repositories import FileSessionRepository, FileEventRepository

__all__ = [
    "SessionManager",
    "Session",
    "EventRecord",
    "SessionStatus",
    "FileSessionRepository",
    "FileEventRepository",
]
```

---

### 四、删除 `src/session/event_store.py`

直接删除该文件，不再保留。

---

## 🧪 验证步骤（Coding Agent 执行后必须通过）

1. **运行 Session 模块测试**
   ```bash
   python -m pytest tests/new_arch/test_session.py -v
   ```
   预期：7 tests passed

2. **运行所有新架构测试**
   ```bash
   python -m pytest tests/new_arch/ -v
   ```
   预期：31 tests passed（含 Session 7 个）

3. **检查导入其他模块是否报错**
   ```bash
   python -c "from src.orchestration import Orchestrator; print('OK')"
   python -c "from src.harness import Harness; print('OK')"
   python -c "from src.migration import LegacySessionConverter; print('OK')"
   ```

---

## 📋 修改影响范围总结

| 模块/文件 | 影响 | 操作 |
|-----------|------|------|
| `src/session/event_store.py` | 删除 | 直接删除 |
| `src/session/session_manager.py` | 完全重写 | 替换为上述新内容 |
| `src/session/__init__.py` | 导出变更 | 替换为上述新内容 |
| `src/session/repositories.py` | 新增 | 创建文件并写入上述内容 |
| `src/orchestration/` | 无影响 | 无需修改（接口不变） |
| `src/harness/` | 无影响 | 无需修改 |
| `src/migration/` | 微小适配（可选） | 若内部直接实例化 `EventStore`，改为 `SessionManager`；若通过依赖注入则无需改动 |
| `tests/new_arch/test_session.py` | 无影响 | 测试应全部通过 |

---

## ⚡ 执行建议

1. **先备份当前 `src/session/` 目录**。
2. 按照上述“文件操作清单”顺序执行新建、替换、删除操作。
3. 执行验证步骤确保功能完整。
4. 提交代码，附带 commit message：
   ```
   refactor(session): decouple session management from persistence layer

   - Remove EventStore (mixed responsibilities)
   - Introduce FileSessionRepository and FileEventRepository
   - SessionManager now directly implements ISession with business logic
   - External interfaces remain unchanged
   ```

如遇任何测试失败，请检查 `models.py` 中的 `Session.from_dict` 和 `EventRecord.from_dict` 是否与新仓库的序列化格式兼容（应无需改动，因为数据格式相同）。

---
