"""
ChannelRouter —— 渠道路由器，按 channel_type 将传出消息分发到正确的渠道实现。

============================================================================
设计意图
============================================================================

  编排层不知道消息最终通过哪个渠道发送（QQ / Web / Console），
  它只调用 ChannelRouter.send(message)。
  路由器根据 message.channel_type 查找注册的渠道，委托其发送。

  这实现了:
    1. 编排层与渠道解耦（新增渠道不改编排代码）
    2. 统一的消息发送入口（便于日志、重试、降级）

============================================================================
使用示例
============================================================================

  router = ChannelRouter()
  router.register(ChannelType.NAPCAT, napcat_channel)
  router.register(ChannelType.CONSOLE, console_channel)

  # 编排层调用
  await router.send(unified_message)  # 自动路由到对应渠道
"""
from typing import Dict, Optional

from common.types import ChannelType, UnifiedMessage
from common.interfaces import IChannel


class ChannelRouter:
    """将传出消息按 channel_type 路由到正确的 IChannel 实现。

    Attributes:
        _channels: channel_type → IChannel 实例的映射表。
    """

    def __init__(self):
        self._channels: Dict[ChannelType, IChannel] = {}

    def register(self, channel_type: ChannelType, channel: IChannel) -> None:
        """注册一个渠道实现。

        Args:
            channel_type: 渠道类型枚举。
            channel: 渠道实例（需实现 IChannel 接口）。
        """
        self._channels[channel_type] = channel

    def get(self, channel_type: ChannelType) -> Optional[IChannel]:
        """按类型获取渠道实例。

        Args:
            channel_type: 渠道类型枚举。

        Returns:
            渠道实例，未注册时返回 None。
        """
        return self._channels.get(channel_type)

    async def send(self, message: UnifiedMessage) -> bool:
        """向消息所属渠道发送统一消息。

        从 message.channel_type 查找对应渠道 → 委托 send_message()。

        Args:
            message: 统一消息对象。

        Returns:
            True 表示发送成功，False 表示渠道未注册或发送失败。
        """
        channel = self._channels.get(message.channel_type)
        if not channel:
            return False
        return await channel.send_message(message)
