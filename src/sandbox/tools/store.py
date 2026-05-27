"""ToolFileStore —— JSON/YAML 工具定义文件的本地存储管理器。

职责:
  1. 从 JSON 文件加载工具元数据
  2. 从元数据 + 执行函数构建 StructuredTool
  3. 将运行时工具实例导出为 JSON 文件
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model


class ToolFileStore:
    """工具定义文件的本地存储管理器。

    Usage:
        store = ToolFileStore(base_path="tools/definitions")
        defs = store.list_definitions("native")
        tool = store.build_tool_from_definition(defs[0], my_execute_func)
    """

    def __init__(self, base_path: str = "tools/definitions"):
        self._base = Path(base_path)

    # ── 读取 ─────────────────────────────────────────────────────

    def list_definitions(self, category: Optional[str] = None) -> List[dict]:
        definitions = []
        search_path = self._base / category if category else self._base
        if not search_path.exists():
            return []
        for file_path in sorted(search_path.rglob("*.json")):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                data["_source_path"] = str(file_path)
                definitions.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return definitions

    def load_tool_definition(self, name: str, category: str = "native") -> Optional[dict]:
        file_path = self._base / category / f"{name}.json"
        if not file_path.exists():
            return None
        return json.loads(file_path.read_text(encoding="utf-8"))

    # ── 写入 ─────────────────────────────────────────────────────

    def save_definition(self, definition: dict, category: str = "custom") -> str:
        target_dir = self._base / category
        target_dir.mkdir(parents=True, exist_ok=True)
        name = definition.get("name", "unnamed")
        file_path = target_dir / f"{name}.json"
        file_path.write_text(
            json.dumps(definition, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(file_path)

    # ── 构建工具 ─────────────────────────────────────────────────

    def build_tool_from_definition(self, definition: dict, tool_instance) -> StructuredTool:
        """从 JSON 定义 + 已有的 BaseTool 实例关联执行逻辑。

        因为 StructuredTool.from_function() 内部已绑定 coroutine，
        此方法用于将从 JSON 重新构建 args_schema 并创建新 tool。

        如果传入的是已有 StructuredTool，直接用其 coroutine + 新的 args_schema。
        """
        params = definition.get("parameters", {})
        fields = {}
        for prop_name, prop_schema in params.get("properties", {}).items():
            prop_type = self._json_type_to_python(prop_schema.get("type", "string"))
            default = prop_schema.get("default")
            description = prop_schema.get("description", "")
            if default is not None:
                fields[prop_name] = (prop_type, Field(default=default, description=description))
            elif prop_name in (params.get("required") or []):
                fields[prop_name] = (prop_type, Field(description=description))
            else:
                fields[prop_name] = (Optional[prop_type], Field(default=None, description=description))

        model_name = f"{definition['name']}_args".replace("-", "_")
        ArgsModel = create_model(model_name, **fields)

        coro = tool_instance._arun if hasattr(tool_instance, "_arun") else tool_instance.ainvoke

        return StructuredTool.from_function(
            name=definition["name"],
            description=definition.get("description", ""),
            args_schema=ArgsModel,
            coroutine=coro,
            metadata=definition.get("metadata", {}),
        )

    @staticmethod
    def _json_type_to_python(json_type: str):
        from typing import Optional  # noqa: re-import for create_model scope
        mapping = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        return mapping.get(json_type, str)
