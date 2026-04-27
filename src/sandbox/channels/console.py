from typing import Any, Optional
from sandbox.channels.base import BaseChannel, ChannelMessage
from common.types import ChannelType, UnifiedMessage


class ConsoleChannel(BaseChannel):
    """
    控制台协议通道：通过标准输入和输出与用户交互。
    """
    def __init__(self):
        super().__init__(ChannelType.CONSOLE)
        self._input_queue = []
        self._running = False

    async def normalize_message(self, raw_message: Any) -> UnifiedMessage:
        if isinstance(raw_message, str):
            return UnifiedMessage(sender_id="console_user", channel_type=ChannelType.CONSOLE, content=raw_message)
        elif isinstance(raw_message, dict):
            return UnifiedMessage(message_id=raw_message.get("message_id", ""), sender_id=raw_message.get("sender_id", "console_user"), channel_type=ChannelType.CONSOLE, content=raw_message.get("content", ""), metadata=raw_message.get("metadata", {}))
        else:
            return UnifiedMessage(sender_id="console_user", channel_type=ChannelType.CONSOLE, content=str(raw_message))

    async def send_message(self, message: UnifiedMessage) -> bool:
        try:
            print(f"\n[ASSISTANT] {message.content}")
            return True
        except Exception:
            return False

    async def read_input_sync(self) -> Optional[str]:
        try:
            return input("\n[USER] ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    async def start_interactive(self, on_message_callback) -> None:
        self._running = True
        print("Console channel started. Type 'exit' to quit.")
        while self._running:
            user_input = await self.read_input_sync()
            if not user_input:
                break
            if user_input.lower() in ("exit", "quit"):
                self._running = False
                break
            channel_msg = ChannelMessage(sender_id="console_user", content=user_input, channel_type=ChannelType.CONSOLE)
            unified = channel_msg.to_unified_message()
            await on_message_callback(unified)

    def stop(self) -> None:
        self._running = False
