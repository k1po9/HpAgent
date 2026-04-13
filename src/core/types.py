from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class TemplateContext:
    """贯穿整个回复流程的上下文对象（简化版）"""
    body: str
    session_key: str
    provider: str = "console"
    from_: Optional[str] = None
    to: Optional[str] = None
    reply_to_id: Optional[str] = None
    media_urls: List[str] = field(default_factory=list)
    chat_type: str = "direct"
    conversation_history: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class ReplyPayload:
    """最终回复的内容载体"""
    text: str
    is_error: bool = False
