"""
Sandbox —— 手层（Hands Layer），工具选择 + 安全执行 + 输出后处理。

在"手脑分离"架构中:
  编排层（大脑）→  决策何时调用工具
  沙箱层（手）  →  工具选择管线 + 安全路由执行 + 输出截断 + 跨轮 hints 状态

子模块:
  sandbox.py           select_tools() + execute() + hints 状态管理
  sandbox_manager.py   按会话创建 / 查询 / 空闲回收
  nsjail.py            可选 nsjail 加固（仅对 Bash 工具）
  tools/               工具体系
    local/             ← 6 个 workspace 本地工具
    adapters/          ← MCP 适配器
    skills/            ← Skills 引擎
    registry.py        ← ToolRegistry（三槽位注册 + RAG 检索）
    types.py           ← ToolResult
    retriever.py       ← ChromaDB 向量存储 + 语义检索
  channels/            渠道通信
"""
from .sandbox import Sandbox
from .sandbox_manager import SandboxManager
from .nsjail import NsjailConfig, NsjailExecutor

__all__ = ["Sandbox", "SandboxManager", "NsjailConfig", "NsjailExecutor"]
