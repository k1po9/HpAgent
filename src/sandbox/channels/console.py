"""
ConsoleChannel —— 控制台协议渠道，通过标准输入输出与用户交互。

============================================================================
使用场景
============================================================================

  - 本地开发调试（无需启动 QQ / WebSocket 服务）
  - CLI 交互式测试 agentic loop
  - 最简单的渠道实现参考

============================================================================
交互方式
============================================================================

  start_interactive() → 循环读取 stdin → normalize → callback → 打印回复
  输入 "exit" 或 "quit" 退出交互循环。
  Ctrl+C / Ctrl+D 也会安全退出。
"""
from typing import Any, Optional
from sandbox.channels.base import BaseChannel, ChannelMessage
from common.types import ChannelType, UnifiedMessage


class ConsoleChannel(BaseChannel):
    """控制台协议通道 —— 通过标准输入和输出与用户交互。

    支持两种输入格式:
      - str: 纯文本消息
      - dict: 结构化消息（含 message_id / sender_id / content / metadata）

    Attributes:
        _running: 交互循环是否正在运行。
    """

    def __init__(self):
        super().__init__(ChannelType.CONSOLE)
        self._running = False

    async def normalize_message(self, raw_message: Any) -> UnifiedMessage:
        """将控制台输入标准化为 UnifiedMessage。

        支持三种输入格式:
          - str: 纯文本 → sender_id="console_user"
          - dict: 结构化消息 → 提取 message_id / sender_id / content / metadata
          - 其他: 转为字符串 → sender_id="console_user"

        Args:
            raw_message: 原始输入（str / dict / 其他）。

        Returns:
            标准化的 UnifiedMessage。
        """
        if isinstance(raw_message, str):
            return UnifiedMessage(
                sender_id="console_user",
                channel_type=ChannelType.CONSOLE,
                content=raw_message,
            )
        elif isinstance(raw_message, dict):
            return UnifiedMessage(
                message_id=raw_message.get("message_id", ""),
                sender_id=raw_message.get("sender_id", "console_user"),
                channel_type=ChannelType.CONSOLE,
                content=raw_message.get("content", ""),
                metadata=raw_message.get("metadata", {}),
            )
        else:
            return UnifiedMessage(
                sender_id="console_user",
                channel_type=ChannelType.CONSOLE,
                content=str(raw_message),
            )

    async def send_message(self, message: UnifiedMessage) -> bool:
        """将回复打印到标准输出。

        Args:
            message: 统一消息对象。

        Returns:
            True 表示打印成功，False 表示异常。
        """
        try:
            print(f"\n[ASSISTANT] {message.content}")
            return True
        except Exception:
            return False

    async def read_input_sync(self) -> Optional[str]:
        """同步读取一行用户输入。

        包装了 input() 的异常处理（EOFError / KeyboardInterrupt）。

        Returns:
            用户输入的字符串，EOF/中断时返回 None。
        """
        try:
            return input("\n[USER] ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    async def start_interactive(self, on_message_callback) -> None:
        """启动交互式循环 —— 持续读取输入 → 回调处理 → 打印回复。

        这是 ConsoleChannel 的核心入口。循环直到:
          - 用户输入 "exit" / "quit"
          - read_input_sync() 返回 None（EOF / 中断）

        Args:
            on_message_callback: 消息处理回调 async func(UnifiedMessage)。
        """
        self._running = True
        print("Console channel started. Type 'exit' to quit.")
        while self._running:
            user_input = await self.read_input_sync()
            if not user_input:
                break
            if user_input.lower() in ("exit", "quit"):
                self._running = False
                break
            # 构造渠道原生消息 → 转换为统一格式 → 回调
            channel_msg = ChannelMessage(
                sender_id="console_user",
                content=user_input,
                channel_type=ChannelType.CONSOLE,
            )
            unified = channel_msg.to_unified_message()
            await on_message_callback(unified)

    def stop(self) -> None:
        """停止交互循环。"""
        self._running = False
