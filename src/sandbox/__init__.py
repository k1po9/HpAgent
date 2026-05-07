"""
Sandbox —— 手层（Hands Layer），负责所有外部操作的实际执行。

============================================================================
在"手脑分离"架构中的角色
============================================================================

  编排层（大脑）  →  决策"该调用哪个工具 / 该向哪个渠道发消息"
  沙箱层（手）    →  真正执行工具调用 / 真正通过渠道 I/O 发送消息

  大脑不直接触碰外部世界，所有副作用操作都经过沙箱代理。
  这保证了:
    1. 工具执行可隔离（nsjail OS 级权限控制、超时、资源限制）
    2. 渠道适配可插拔（新增渠道不影响编排逻辑）
    3. 测试时可替换为 mock sandbox

============================================================================
子模块结构
============================================================================

  sandbox.py           单个沙箱实例 —— 工具注册表 + nsjail 执行入口 + 生命周期
  sandbox_manager.py   沙箱管理器 —— 多沙箱池、空闲回收、健康检查
  nsjail.py            nsjail 集成 —— NsjailConfig + NsjailExecutor
  runner.py            in-jail 工具执行脚本（在 nsjail 命名空间内运行）
  channels/            渠道适配层
    ├── base.py        渠道基类（BaseChannel / ChannelMessage）
    ├── console.py     控制台渠道（stdin/stdout 交互）
    ├── napcat.py      NapCat QQ 渠道（WebSocket 连接 OneBot v11）
    └── router.py      渠道路由器（按 ChannelType 分发消息）
  tools/               工具系统
    ├── base.py        工具基类（BaseTool / ToolDefinition / ToolResult / ToolType）
    ├── registry.py    工具注册表（线程安全的增删查）
    ├── factory.py     工具工厂（DynamicTool + 内置工具创建）
    └── __init__.py    工具模块导出
"""
from .sandbox import Sandbox
from .sandbox_manager import SandboxManager
from .nsjail import NsjailConfig, NsjailExecutor

__all__ = ["Sandbox", "SandboxManager", "NsjailConfig", "NsjailExecutor"]
