"""
Channels —— 渠道适配层，将不同消息平台的消息统一为 UnifiedMessage。

============================================================================
设计意图
============================================================================

  不同消息平台（QQ/NapCat、Web、Console）有不同的消息格式和通信协议。
  渠道层负责:
    1. 接收原始消息 → normalize_message() → UnifiedMessage（标准化）
    2. 发送 UnifiedMessage → 转换为平台格式 → 发送到外部

  这样编排层只需处理 UnifiedMessage，不关心底层平台差异。

============================================================================
渠道实现
============================================================================

  BaseChannel     — 渠道抽象基类（定义 normalize_message / send_message 接口）
  ConsoleChannel  — 控制台渠道（stdin/stdout 交互，开发调试用）
  NapCatChannel   — NapCat QQ 渠道（WebSocket 连接 OneBot v11 协议）
  ChannelRouter   — 渠道路由器（按 ChannelType 分发消息到对应渠道）

============================================================================
扩展新渠道
============================================================================

  1. 继承 BaseChannel
  2. 实现 normalize_message() 和 send_message()
  3. 在 ChannelRouter 注册: router.register(ChannelType.WEB, web_channel)
  4. 在 worker.py 中初始化并注册
"""
from .base import BaseChannel
from .console import ConsoleChannel
from .napcat import NapCatChannel
from .official_qq import OfficialQQChannel
from .router import ChannelRouter

__all__ = ["BaseChannel", "ConsoleChannel", "NapCatChannel", "OfficialQQChannel", "ChannelRouter"]
