"""
BaseChannel —— 渠道抽象基类，定义所有消息渠道的通用接口。

============================================================================
核心抽象
============================================================================

  ChannelMessage  — 渠道原生消息的数据类，在渠道内部使用
  BaseChannel     — 渠道抽象基类（ABC），定义:
    - normalize_message():  原始消息 → UnifiedMessage（标准化）
    - send_message():       UnifiedMessage → 平台格式 → 发送
    - start_monitor():      启动消息监听（接收消息的入口）
    - stop_monitor():       停止消息监听

============================================================================
消息流转
============================================================================

  外部平台消息
    → normalize_message()   # 标准化为 UnifiedMessage
    → callback()            # 传递给 Worker 的消息处理回调
    → [agentic loop 处理]
    → ChannelRouter.send()  # 路由到对应渠道
    → send_message()        # 转换为平台格式并发送
    → 外部平台

============================================================================
数据类说明
============================================================================

  ChannelMessage:
    - 渠道内部使用的消息格式，包含原始平台的所有字段
    - to_unified_message() 将其转换为系统统一的 UnifiedMessage
    - 每个渠道可以根据需要扩展 metadata 字段
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field
import uuid
import time
from common.types import ChannelType, UnifiedMessage
from common.interfaces import IChannel


@dataclass
class ChannelMessage:
    """渠道原生消息的数据类 —— 在渠道内部使用，标准化前的中转格式。

    Attributes:
        message_id: 消息唯一标识（默认 UUID）。
        sender_id: 发送者 ID（平台原始 ID，如 QQ 号）。
        content: 消息文本内容。
        channel_type: 渠道类型枚举。
        timestamp: 消息时间戳。
        metadata: 渠道特定元数据（如 group_id、post_type 等）。
        media_urls: 媒体附件 URL 列表。
        reply_to_id: 被回复消息的 ID（用于引用回复）。
    """
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender_id: str = ""
    content: str = ""
    channel_type: ChannelType = ChannelType.CONSOLE
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    media_urls: list = field(default_factory=list)
    reply_to_id: Optional[str] = None

    def to_unified_message(self, session_id: str = "") -> UnifiedMessage:
        """将渠道原生消息转换为系统统一的 UnifiedMessage。

        Args:
            session_id: 关联的会话 ID。

        Returns:
            统一消息格式，供编排层消费。
        """
        return UnifiedMessage(
            message_id=self.message_id,
            session_id=session_id,
            sender_id=self.sender_id,
            channel_type=self.channel_type,
            content=self.content,
            timestamp=self.timestamp,
            metadata=self.metadata,
            media_urls=self.media_urls,
        )


class BaseChannel(IChannel, ABC):
    """渠道抽象基类 —— 所有消息渠道的公共接口。

    每个具体渠道实现需提供:
      - normalize_message(): 将平台原始消息格式化为 UnifiedMessage
      - send_message(): 将 UnifiedMessage 转换回平台格式并发送

    Attributes:
        _channel_type: 渠道类型枚举。
        _monitoring: 是否正在监听消息。
        _callback: 收到新消息时的回调函数（由 Worker 注入）。
    """

    def __init__(self, channel_type: ChannelType):
        """初始化渠道。

        Args:
            channel_type: 渠道类型枚举值。
        """
        self._channel_type = channel_type
        self._monitoring = False
        self._callback: Optional[Callable[[UnifiedMessage], Awaitable[None]]] = None

    @property
    def channel_type(self) -> ChannelType:
        """返回渠道类型。"""
        return self._channel_type

    @abstractmethod
    async def normalize_message(self, raw_message: Any) -> UnifiedMessage:
        """将平台原始消息标准化为 UnifiedMessage。

        Args:
            raw_message: 平台原始消息（格式因渠道而异: str / dict / bytes）。

        Returns:
            标准化的 UnifiedMessage。
        """
        ...

    @abstractmethod
    async def send_message(self, message: UnifiedMessage) -> bool:
        """发送消息到外部平台。

        Args:
            message: 统一消息对象。

        Returns:
            True 表示发送成功，False 表示失败。
        """
        ...

    async def start_monitor(self, callback_url: str) -> bool:
        """启动消息监听（默认实现）。

        Args:
            callback_url: 回调地址或回调函数（具体含义由子类定义）。

        Returns:
            True 表示启动成功。
        """
        self._monitoring = True
        self._callback = None
        return True

    async def stop_monitor(self) -> bool:
        """停止消息监听（默认实现）。

        Returns:
            True 表示停止成功。
        """
        self._monitoring = False
        self._callback = None
        return True

    @property
    def is_monitoring(self) -> bool:
        """返回是否正在监听消息。"""
        return self._monitoring
