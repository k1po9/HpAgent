"""
TemporalSessionManager —— 基于 Temporal Workflow Query 的会话管理器。

============================================================================
设计意图
============================================================================

  旧版 SessionManager（约 330 行）直接操作文件 / PostgreSQL 存储。
  重构后:
    - 持久化存储 → src/storage/（protocols.py + 后端实现）
    - 会话事件查询 → Temporal Workflow 的 Query 机制（运行中 Workflow 的事件历史）
    - 会话元数据 → 本地轻量字典缓存（_sessions）

  TemporalSessionManager 实现了 ISession 接口，可无缝替换旧的 SessionManager。

============================================================================
核心差异：Temporal 模式 vs 文件模式
============================================================================

  旧版（文件/SQL）:
    create_session() → 写入文件/数据库
    emit_event()     → 追加到文件/数据库
    get_events()     → 从文件/数据库读取

  Temporal 模式:
    create_session() → 记录到本地字典（元数据缓存）
    emit_event()     → 无操作（Temporal Workflow 自动记录事件历史）
    get_events()     → 通过 Workflow Query "get_events" 读取

============================================================================
注意事项
============================================================================

  - 写操作（create_session / emit_event）在 Temporal 模式下为轻量操作或空操作
  - 真正的数据持久化由 Temporal Server 负责（Workflow Event History）
  - 如果 Temporal 不可用，get_events() 返回空列表（静默降级）
"""
from typing import Dict, List, Optional, Any
import time

from common.types import Event, SessionMetadata, ChannelType, EventType
from common.interfaces import ISession


class TemporalSessionManager(ISession):
    """Temporal 感知的会话管理器 —— 通过 Workflow Query 读取事件历史。

    实现了 ISession 接口，可注入到任何依赖 ISession 的代码中。
    写操作（emit_event / create_session）在当前模式下为轻量实现，
    因为 Temporal 自动管理 Workflow 的事件历史。

    Attributes:
        _temporal_client: Temporal Client 实例（用于发起 Workflow Query）。
        _sessions: 本地会话元数据缓存（session_id → 元数据字典）。
    """

    def __init__(self, temporal_client=None):
        """初始化 Temporal 会话管理器。

        Args:
            temporal_client: Temporal Client 实例。
                             Worker 启动时由 start_worker() 传入。
                             如果为 None，get_events() 将返回空列表（降级）。
        """
        self._temporal_client = temporal_client
        self._sessions: Dict[str, Dict[str, Any]] = {}

    # ═══════════════════════════════════════════════════════════════════
    # 读操作 —— 委托给 Temporal Workflow Query
    # ═══════════════════════════════════════════════════════════════════

    async def get_events(
        self,
        session_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[Event]:
        """从运行中的 OrchestrationWorkflow 通过 Query 获取事件。

        流程:
          1. 通过 workflow_id = f"agent-{session_id}" 获取 Workflow 句柄
          2. 发起 Query "get_events" 获取完整事件列表
          3. 按 event_types 过滤（可选，大小写不敏感）
          4. 分页切片（offset / limit）

        Args:
            session_id: 会话 ID（也是 workflow_id 的一部分）。
            offset: 分页偏移量。
            limit: 返回事件数量上限（None 表示不限制）。
            event_types: 事件类型过滤列表（如 ["user_message", "tool_call"]）。

        Returns:
            Event 对象列表。如果 Temporal 不可用或 Workflow 不存在，返回空列表。
        """
        if not self._temporal_client:
            return []
        try:
            # workflow_id 规则: f"agent-{session_id}"
            handle = self._temporal_client.get_workflow_handle(
                f"agent-{session_id}"
            )
            all_events = await handle.query("get_events")
        except Exception:
            # Workflow 不存在或 Query 失败 → 静默降级返回空列表
            return []

        # 按事件类型过滤（大小写不敏感）
        if event_types:
            all_events = [
                e for e in all_events
                if e.get("type", "").upper() in [et.upper() for et in event_types]
            ]

        # 分页
        result = all_events[offset:]
        if limit is not None:
            result = result[:limit]

        # 转换为 common.types.Event 对象
        return [
            Event(
                event_id=e.get("event_id", ""),
                session_id=session_id,
                timestamp=e.get("timestamp", 0),
                event_type=EventType(e.get("type", "user_message")),
                content=e.get("content", {}),
                metadata=e.get("metadata", {}),
            )
            for e in result
        ]

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[SessionMetadata]:
        """列出本地缓存的会话元数据。

        注意: 此方法仅列出本地字典中的会话（Worker 内存中），
        不会查询 Temporal 或外部存储。生产环境应考虑接入 storage/ 层。

        Args:
            limit: 返回数量上限。
            offset: 分页偏移量。
            status: 状态过滤（当前未实现）。
            tags: 标签过滤（当前未实现）。

        Returns:
            SessionMetadata 对象列表，按创建时间降序排列。
        """
        sessions = list(self._sessions.values())
        sessions.sort(key=lambda s: s.get("created_at", 0), reverse=True)
        return [
            SessionMetadata(
                session_id=s["session_id"],
                creator_id=s.get("creator_id", ""),
                channel_type=ChannelType(s.get("channel_type", "console")),
                tags=s.get("tags", []),
                created_at=s.get("created_at", time.time()),
                status=s.get("status", "active"),
            )
            for s in sessions[offset : offset + limit]
        ]

    # ═══════════════════════════════════════════════════════════════════
    # 写操作 —— Temporal 模式下为轻量/空操作
    # ═══════════════════════════════════════════════════════════════════

    async def create_session(self, metadata: SessionMetadata) -> str:
        """创建会话 —— 记录元数据到本地缓存。

        Temporal 模式下，真正的会话状态由 Workflow 管理。
        这里仅缓存元数据供 list_sessions() 查询。

        Args:
            metadata: 会话元数据。

        Returns:
            session_id。
        """
        self._sessions[metadata.session_id] = metadata.to_dict()
        return metadata.session_id

    async def emit_event(self, event: Event) -> str:
        """记录事件 —— 空操作。

        Temporal Workflow 自动管理事件历史，无需显式写入。
        保留此方法仅为满足 ISession 接口兼容性。

        Args:
            event: 事件对象。

        Returns:
            event_id。
        """
        return event.event_id

    async def rewind_session(
        self, session_id: str, target_event_id: str
    ) -> Dict[str, Any]:
        """回溯会话 —— 占位实现。

        Temporal 模式下回溯需要操作 Workflow History，较复杂。
        当前返回空结果。

        Args:
            session_id: 会话 ID。
            target_event_id: 目标事件 ID（回退到此事件之后）。

        Returns:
            包含回溯信息的字典。
        """
        return {
            "session_id": session_id,
            "rewound_to_event_id": target_event_id,
            "removed_events_count": 0,
        }

    async def archive_session(self, session_id: str) -> bool:
        """归档会话 —— 标记本地缓存中的会话为 archived。

        Args:
            session_id: 会话 ID。

        Returns:
            True 表示操作成功。
        """
        if session_id in self._sessions:
            self._sessions[session_id]["status"] = "archived"
        return True
