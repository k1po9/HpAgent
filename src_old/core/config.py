from dataclasses import dataclass, field
from typing import Optional

@dataclass
class LoopConfig:
    max_turns: int = 20

@dataclass
class ModelConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: Optional[str] = None
    max_retries: int = 2
    timeout_seconds: int = 30


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

@dataclass
class AppConfig:
    loop: LoopConfig = field(default_factory=LoopConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    tool: ToolConfig = field(default_factory=ToolConfig)
    max_history_turns: int = 10
    max_turns: int = 20
    system_prompt: str = "You are a helpful assistant."
