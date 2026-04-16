from typing import Any, Optional
from .config import ToolConfig
from .protocol import Tool, Skill
from .registry import NativeToolRegistry, MCPToolRegistry, SkillRegistry
from .factory import ToolFactory
from .validator import ParamValidator


class ToolService:
    """工具总服务：Agent唯一调用入口"""
    
    def __init__(self, config: ToolConfig):
        self.config = config
        self.native_registry = NativeToolRegistry()
        self.mcp_registry = MCPToolRegistry()
        self.skill_registry = SkillRegistry()
        self.factory = ToolFactory()
        self.validator = ParamValidator()
    
    def list_all_tools(self) -> list[dict]:
        tools = []
        if self.config.enable_native:
            tools.extend(self.native_registry.list_definitions())
        if self.config.enable_mcp:
            tools.extend(self.mcp_registry.list_definitions())
        return tools
    
    def register_native(self, tool: Tool) -> None:
        self.native_registry.register(tool)
    
    def register_mcp(self, tool: Tool) -> None:
        self.mcp_registry.register(tool)
    
    def register_skill(self, skill: Skill) -> None:
        self.skill_registry.register(skill)
    
    def unregister_native(self, name: str) -> bool:
        return self.native_registry.unregister(name)
    
    def unregister_mcp(self, name: str) -> bool:
        return self.mcp_registry.unregister(name)
    
    def unregister_skill(self, name: str) -> bool:
        return self.skill_registry.unregister(name)
    
    def get_tool(self, name: str) -> Optional[Tool]:
        tool = self.native_registry.get(name)
        if tool:
            return tool
        return self.mcp_registry.get(name)
    
    def validate_params(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, list[str]]:
        tool = self.get_tool(tool_name)
        if not tool:
            return False, [f"Tool '{tool_name}' not found"]
        
        if self.config.validate_before_execute:
            return self.validator.validate(params, tool.parameters)
        
        return True, []
    
    async def execute_tool(self, name: str, params: dict[str, Any]) -> Any:
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Tool '{name}' not found")
        
        skills = self.skill_registry.get_by_tool(name)
        for skill in skills:
            result = await skill.apply({"tool": name, "params": params})
            if result.get("modified"):
                pass
        
        valid, errors = self.validate_params(name, params)
        if not valid:
            raise ValueError(f"Validation failed: {', '.join(errors)}")
        
        return await tool.execute(**params)
    
    def bind_skill_to_tool(self, skill_name: str, tool_name: str) -> None:
        skill = self.skill_registry.get(skill_name)
        if not skill:
            raise ValueError(f"Skill '{skill_name}' not found")
        
        skill.bound_tool_name = tool_name
        if tool_name not in [s.bound_tool_name for s in self.skill_registry.list_all() if s.bound_tool_name == tool_name]:
            self.skill_registry.register(skill)
    
    async def create_native_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        execute_func,
    ) -> Tool:
        tool = await self.factory.create_native_tool(name, description, parameters, execute_func)
        self.register_native(tool)
        return tool
    
    async def create_mcp_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        server_name: str,
        mcp_method: str,
    ) -> Tool:
        tool = await self.factory.create_mcp_tool(name, description, parameters, server_name, mcp_method)
        self.register_mcp(tool)
        return tool
