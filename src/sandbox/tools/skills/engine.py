"""
SkillPipeline —— Skills 复合工具编排引擎。

将多个工具按顺序编排为高级能力 —— 对 LLM 暴露为单个工具，
内部由流水线步骤顺序执行。
"""
import re
from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, create_model

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


def _json_to_python(json_type: str):
    """将 JSON Schema type 映射为 Python 类型。"""
    _map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return _map.get(json_type, str)


def build_skill_tool(pipeline: SkillPipeline, registry, parameters: dict = None) -> BaseTool:
    """将 SkillPipeline 包装为 LangChain BaseTool —— LLM 视为普通工具调用。"""

    async def _execute_skill(**kwargs) -> ToolResult:
        return await pipeline.execute(registry, **kwargs)

    # 从 YAML parameters 构建 Pydantic 字段
    fields = {}
    if parameters:
        props = parameters.get("properties", {})
        required_fields = set(parameters.get("required", []))
        for prop_name, prop_schema in props.items():
            prop_type = _json_to_python(prop_schema.get("type", "string"))
            desc = prop_schema.get("description", "")
            if prop_name in required_fields:
                fields[prop_name] = (prop_type, Field(description=desc))
            else:
                fields[prop_name] = (Optional[prop_type], Field(default=None, description=desc))

    SkillArgs = create_model(
        f"skill_{pipeline.name}_args".replace("-", "_"),
        **fields,
    )

    return StructuredTool.from_function(
        name=pipeline.name,
        description=pipeline.description,
        args_schema=SkillArgs,
        coroutine=_execute_skill,
        metadata={"category": "skill", "side_effect_class": "unknown"},
    )


def _build_instruction_tool(name: str, description: str, body: str) -> BaseTool:
    """构建指令型 skill 工具 —— 调用时返回 SKILL.md body 内容供 LLM 参考。"""

    InstructionArgs = create_model(
        f"skill_{name}_args".replace("-", "_"),
    )

    async def _get_instructions(**kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            output=body,
            metadata={"skill_name": name, "type": "instruction"},
        )

    return StructuredTool.from_function(
        name=name,
        description=description,
        args_schema=InstructionArgs,
        coroutine=_get_instructions,
        metadata={"category": "skill", "side_effect_class": "read_only"},
    )


def build_skill_tool_from_definition(skill_def: dict, registry) -> BaseTool:
    """Current model-tool entry point: only read-only Instruction skills are supported.

    The historical Pipeline engine remains available for legacy code, but cannot
    be built as a model tool through this entry point.
    """
    if skill_def.get("type") != "instruction":
        raise ValueError("only instruction skills can be built as model tools")
    return _build_instruction_tool(
        name=skill_def["name"],
        description=skill_def.get("description", ""),
        body=skill_def.get("body", ""),
    )
