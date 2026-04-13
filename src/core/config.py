from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: Optional[str] = None
    max_retries: int = 2
    timeout_seconds: int = 30


@dataclass
class AppConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    max_history_turns: int = 10
    system_prompt: str = "You are a helpful assistant."
