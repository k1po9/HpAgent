from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolConfig:
    """工具层统一配置（Agent 初始化时传入）"""
    
    enable_native: bool = True
    enable_mcp: bool = False
    enable_skills: bool = True
    
    mcp_servers: list[dict] = field(default_factory=list)
    mcp_timeout: int = 30
    
    default_native_tools: list[str] = field(default_factory=list)
    default_mcp_tools: list[str] = field(default_factory=list)
    default_skills: list[dict] = field(default_factory=list)
    
    validate_before_execute: bool = True
