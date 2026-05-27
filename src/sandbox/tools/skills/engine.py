"""
SkillPipeline —— Skills 复合工具编排引擎。

将多个工具按顺序编排为高级能力 —— 对 LLM 暴露为单个工具，
内部由流水线步骤顺序执行。
"""
import re
from typing import Any, Dict, List

from langchain_core.tools import BaseTool, StructuredTool

from sandbox.tools.types import ToolResult


class SkillPipeline:
    """Skill 流水线编排引擎。

    支持:
      - 步骤间结果引用: $step_id 引用前一步骤的输出
      - 参数模板替换: $param 引用 Skill 输入参数
      - 错误策略: stop（默认）/ continue
    """

    def __init__(
        self,
        name: str,
        description: str,
        steps: List[dict],
        on_error: str = "stop",
        timeout_seconds: float = 60.0,
    ):
        self._name = name
        self._description = description
        self._steps = steps
        self._on_error = on_error
        self._timeout = timeout_seconds

    async def execute(self, registry, **kwargs) -> ToolResult:
        step_outputs: Dict[str, Any] = {}
        step_outputs.update(kwargs)

        for step in self._steps:
            tool_name = step["tool"]
            arguments = self._resolve_arguments(step.get("arguments", {}), step_outputs)

            tool = registry.get(tool_name)
            if tool is None:
                result = ToolResult(
                    success=False,
                    error=f"Skill step tool '{tool_name}' not found",
                )
            else:
                result = await registry.execute(tool_name, arguments)

            step_outputs[step["id"]] = result.output if result.success else result.error

            if not result.success and self._on_error == "stop":
                return ToolResult(
                    success=False,
                    error=f"Step '{step['id']}' failed: {result.error}",
                    metadata={"step_id": step["id"], "step_outputs": step_outputs},
                )

        return ToolResult(
            success=True,
            output=step_outputs,
            metadata={"steps_completed": len(self._steps)},
        )

    @staticmethod
    def _resolve_arguments(arguments: dict, context: dict) -> dict:
        resolved = {}
        for key, value in arguments.items():
            if isinstance(value, str) and "$" in value:
                def _replace(m):
                    var_name = m.group(1)
                    return str(context.get(var_name, m.group(0)))
                resolved[key] = re.sub(r'\$(\w+)', _replace, value)
            else:
                resolved[key] = value
        return resolved

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description


def build_skill_tool(pipeline: SkillPipeline, registry) -> BaseTool:
    """将 SkillPipeline 包装为 LangChain BaseTool —— LLM 视为普通工具调用。"""

    async def _execute_skill(**kwargs) -> ToolResult:
        return await pipeline.execute(registry, **kwargs)

    return StructuredTool.from_function(
        name=pipeline.name,
        description=pipeline.description,
        coroutine=_execute_skill,
    )
