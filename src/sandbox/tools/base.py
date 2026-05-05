"""
Tools Base —— 工具系统的核心抽象（BaseTool / ToolDefinition / ToolResult / ToolType）。

============================================================================
设计意图
============================================================================

  ToolType 枚举区分三种工具来源:
    - NATIVE: 原生 Python 实现（默认）
    - MCP:    通过 Model Context Protocol 连接外部工具服务器
    - SKILL:  组合多个工具的高级能力

  BaseTool 抽象基类要求子类提供:
    - name / description / parameters: 工具元信息（供 LLM 选择工具时使用）
    - execute(**kwargs): 工具的实际执行逻辑

  ToolDefinition 是工具元信息的结构化数据类，可转换为多种格式:
    - to_openai_format(): OpenAI function calling 格式
    - 未来可扩展 Anthropic / Gemini 等格式

  ToolResult 统一工具执行返回值:
    - success=True + output: 正常结果
    - success=False + error: 执行失败信息
    - metadata: 附加元数据（如执行耗时、token 消耗）
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class ToolType(str, Enum):
    """工具类型枚举 —— 区分工具的实现方式。

    NATIVE: 原生 Python 实现，直接在进程内执行。
    MCP:    Model Context Protocol，通过标准化协议连接外部工具服务器。
    SKILL:  技能工具，由多个子工具组合而成的高级能力。
    """
    NATIVE = "native"
    MCP = "mcp"
    SKILL = "skill"


@dataclass
class ToolDefinition:
    """工具的元信息定义 —— 描述工具的名称、功能和参数约束。

    Attributes:
        name: 工具名称（LLM 通过此名称选择工具）。
        description: 工具功能描述（帮助 LLM 判断何时使用该工具）。
        parameters: JSON Schema 格式的参数定义。
        tool_type: 工具类型。
        metadata: 附加元数据（如版本号、作者）。
    """
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    tool_type: ToolType = ToolType.NATIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_openai_format(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling 兼容格式。

        Returns:
            {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    """工具执行的统一返回值。

    Attributes:
        success: 执行是否成功。
        output: 成功时的输出数据（类型由工具定义）。
        error: 失败时的错误信息。
        metadata: 附加元数据（如执行耗时、token 消耗等）。
    """
    success: bool = True
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }


class BaseTool(ABC):
    """工具抽象基类 —— 所有工具必须继承此类并实现抽象方法。

    子类需要定义:
      - name (property):        工具唯一名称
      - description (property): 工具功能描述（供 LLM 选工具时参考）
      - parameters (property):  工具参数 JSON Schema
      - execute(**kwargs):      执行逻辑（异步）

    可选覆盖:
      - tool_type (property): 默认为 NATIVE
      - get_definition():     自定义 ToolDefinition 构造逻辑
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一名称 —— LLM 通过此名称选择工具。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具功能描述 —— 帮助 LLM 判断何时使用该工具。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """工具参数定义 —— JSON Schema 格式的参数约束。"""
        ...

    @property
    def tool_type(self) -> ToolType:
        """工具类型，默认为 NATIVE。MCP/SKILL 工具需覆盖此属性。"""
        return ToolType.NATIVE

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具逻辑（异步）。

        Args:
            **kwargs: 工具参数（由 LLM 根据 parameters schema 生成）。

        Returns:
            ToolResult 实例。
        """
        ...

    def get_definition(self) -> ToolDefinition:
        """获取工具的元信息定义。

        Returns:
            ToolDefinition 实例，包含 name / description / parameters / tool_type。
        """
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            tool_type=self.tool_type,
        )

    def get_openai_format(self) -> Dict[str, Any]:
        """获取 OpenAI function calling 兼容格式的工具定义。"""
        return self.get_definition().to_openai_format()
