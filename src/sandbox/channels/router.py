from typing import Dict, Optional

from common.types import ChannelType, UnifiedMessage
from common.interfaces import IChannel


class ChannelRouter:
    """将传出消息按 channel_type 路由到正确的 IChannel 实现。"""

    def __init__(self):
        self._channels: Dict[ChannelType, IChannel] = {}     # channel_type → IChannel

    def register(self, channel_type: ChannelType, channel: IChannel) -> None:
        self._channels[channel_type] = channel

    def get(self, channel_type: ChannelType) -> Optional[IChannel]:
        return self._channels.get(channel_type)

    async def send(self, message: UnifiedMessage) -> bool:
        channel = self._channels.get(message.channel_type)
        if not channel:
            return False
        return await channel.send_message(message)
