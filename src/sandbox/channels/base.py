"""
BaseChannel —— 渠道抽象基类，定义所有消息渠道的通用接口。

============================================================================
核心抽象
============================================================================

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
"""
from abc import ABC, abstractmethod
from typing import Any, Optional, Callable, Awaitable
from common.types import ChannelType, UnifiedMessage
from common.interfaces import IChannel


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
