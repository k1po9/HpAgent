"""
Tools —— 工具系统，定义 LLM 可调用的工具和执行框架。

============================================================================
工具类型（ToolType）
============================================================================

  NATIVE  — 原生工具：直接在 Python 中实现（如计算器、文件读取）
  MCP     — MCP 工具：通过 Model Context Protocol 连接外部工具服务
  SKILL   — 技能工具：组合多个工具形成的高级能力

============================================================================
核心类
============================================================================

  BaseTool       — 工具抽象基类（定义 name / description / parameters / execute）
  ToolResult     — 工具执行结果（success / output / error / metadata）
  ToolDefinition — 工具定义（可转换为 OpenAI function calling 格式）
  ToolRegistry   — 工具注册表（线程安全的增删查执行）
  ToolFactory    — 工具工厂（DynamicTool 动态创建 + 内置工具）
  DynamicTool    — 动态工具（通过函数引用构造，无需定义新类）

============================================================================
工具定义格式
============================================================================

  OpenAI function calling 格式:
    {
      "type": "function",
      "function": {
        "name": "calculator",
        "description": "Evaluate a mathematical expression",
        "parameters": {
          "type": "object",
          "properties": {"expression": {"type": "string"}},
          "required": ["expression"]
        }
      }
    }
"""
from .base import BaseTool, ToolResult, ToolDefinition, ToolType
from .registry import ToolRegistry
from .factory import ToolFactory, DynamicTool

__all__ = [
    "BaseTool", "ToolResult", "ToolDefinition", "ToolType",
    "ToolRegistry", "ToolFactory", "DynamicTool",
]
