from copy import deepcopy

from .models import RegisteredTool


class ToolSchemaProjector:
    NEXT_TOOL_HINT = {
        "type": "string",
        "description": "简短预测：获得本工具结果后，下一步可能需要的工具类型或场景，用于后续工具检索，不超过30个词",
    }

    def project(self, registered: RegisteredTool) -> dict:
        tool = registered.tool
        raw = tool.to_openai_function() if hasattr(tool, "to_openai_function") else {
            "type": "function", "function": {"name": tool.name, "description": tool.description,
            "parameters": tool.args_schema.model_json_schema() if tool.args_schema else {}},
        }
        result = deepcopy(raw)
        func = result.get("function", result)
        params = func.setdefault("parameters", {"type": "object", "properties": {}})
        props = params.setdefault("properties", {})
        params["properties"] = {"next_tool_hint": self.NEXT_TOOL_HINT, **props}
        # Deliberately optional: retrieval hints must not alter business validation.
        return result
