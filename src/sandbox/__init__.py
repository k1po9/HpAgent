"""
Sandbox —— 手层（Hands Layer），模型的工具执行环境。

在"手脑分离"架构中:
  编排层（大脑）→  决策该调用哪个工具
  沙箱层（手）  →  接收 workspace 路径，按类别路由执行工具

  本地工具（fs_read/fs_write/fs_edit/Glob/Grep/Bash）绑定到 workspace，
  MCP 工具远端执行，Skills 展开为子调用。

子模块:
  sandbox.py           单个沙箱 —— workspace 绑定 + ToolRegistry + 类别路由执行
  sandbox_manager.py   沙箱池管理 —— 按会话创建 / 查询 / 空闲回收
  nsjail.py            可选 nsjail 加固（仅对 Bash 工具）
  tools/               工具体系
    local/             ← 6 个 workspace 本地工具
    adapters/          ← MCP 适配器
    skills/            ← Skills 引擎
    registry.py        ← ToolRegistry
    types.py           ← ToolResult
    retriever.py       ← RAG 检索器
  channels/            渠道通信
"""
from .sandbox import Sandbox
from .sandbox_manager import SandboxManager
from .nsjail import NsjailConfig, NsjailExecutor

__all__ = ["Sandbox", "SandboxManager", "NsjailConfig", "NsjailExecutor"]
