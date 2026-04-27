from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field
import uuid
import time
from common.types import ChannelType, UnifiedMessage
from common.interfaces import IChannel


@dataclass
class ChannelMessage:
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender_id: str = ""
    content: str = ""
    channel_type: ChannelType = ChannelType.CONSOLE
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    media_urls: list = field(default_factory=list)
    reply_to_id: Optional[str] = None

    def to_unified_message(self, session_id: str = "") -> UnifiedMessage:
        return UnifiedMessage(message_id=self.message_id, session_id=session_id, sender_id=self.sender_id, channel_type=self.channel_type, content=self.content, timestamp=self.timestamp, metadata=self.metadata, media_urls=self.media_urls)


class BaseChannel(IChannel, ABC):
    def __init__(self, channel_type: ChannelType):
        self._channel_type = channel_type
        self._monitoring = False
        self._callback: Optional[Callable[[UnifiedMessage], Awaitable[None]]] = None

    @property
    def channel_type(self) -> ChannelType:
        return self._channel_type

    @abstractmethod
    async def normalize_message(self, raw_message: Any) -> UnifiedMessage: ...

    @abstractmethod
    async def send_message(self, message: UnifiedMessage) -> bool: ...

    async def start_monitor(self, callback_url: str) -> bool:
        self._monitoring = True
        self._callback = None
        return True

    async def stop_monitor(self) -> bool:
        self._monitoring = False
        self._callback = None
        return True

    @property
    def is_monitoring(self) -> bool:
        return self._monitoring
