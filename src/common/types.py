from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import uuid
import time


class EventType(str, Enum):
    """事件类型枚举"""
    USER_MESSAGE = "user_message"      # 用户发送的消息
    MODEL_MESSAGE = "model_message"    # 模型生成的回复
    TOOL_CALL = "tool_call"            # 模型请求调用工具
    TOOL_RESULT = "tool_result"        # 工具执行返回的结果
    ERROR = "error"                    # 系统错误事件
    CONFIG_CHANGE = "config_change"    # 配置变更事件
    SESSION_START = "session_start"    # 会话开始
    SESSION_COMPLETE = "session_complete"  # 会话正常结束
    SESSION_ARCHIVED = "session_archived"  # 会话被归档
    LOOP_STARTED = "loop_started"      # 推理循环开始
    LOOP_COMPLETED = "loop_completed"  # 推理循环完成
    TURN_COMPLETED = "turn_completed"  # 单轮交互完成


class ChannelType(str, Enum):
    """消息渠道类型枚举"""
    TELEGRAM = "telegram"   # Telegram 平台
    DISCORD = "discord"     # Discord 平台
    SLACK = "slack"         # Slack 平台
    WECHAT = "wechat"       # 微信平台
    WEB = "web"             # Web 页面
    CONSOLE = "console"     # 控制台终端


class StopReason(str, Enum):
    """模型停止生成的原因枚举"""
    END_TURN = "end_turn"       # 模型主动结束回复
    TOOL_USE = "tool_use"       # 模型请求调用工具，暂停生成
    MAX_TOKENS = "max_tokens"   # 达到最大 token 限制
    REFUSAL = "refusal"         # 模型拒绝回答
    ERROR = "error"             # 生成过程发生错误


class ErrorSeverity(str, Enum):
    """错误严重级别枚举"""
    RECOVERABLE = "recoverable"  # 可恢复错误，系统可继续运行
    FATAL = "fatal"              # 致命错误，系统无法继续


@dataclass
class Event:
    """系统内部通用事件，用于在各组件间传递信息"""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))   # 事件唯一标识
    session_id: str = ""                                               # 所属会话 ID
    timestamp: float = field(default_factory=time.time)                # 事件发生时间戳
    event_type: EventType = EventType.USER_MESSAGE                     # 事件类型
    content: Dict[str, Any] = field(default_factory=dict)              # 事件核心数据
    metadata: Dict[str, Any] = field(default_factory=dict)             # 事件的附加元数据

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value if isinstance(self.event_type, Enum) else self.event_type,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        event_type = data.get("event_type")
        if isinstance(event_type, str):
            event_type = EventType(event_type)
        return cls(
            event_id=data.get("event_id", str(uuid.uuid4())),
            session_id=data.get("session_id", ""),
            timestamp=data.get("timestamp", time.time()),
            event_type=event_type or EventType.USER_MESSAGE,
            content=data.get("content", {}),
            metadata=data.get("metadata", {}),
        )


@dataclass
class UnifiedMessage:
    """统一消息格式，将不同渠道的消息标准化为统一结构"""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))  # 消息唯一标识
    session_id: str = ""                                                # 所属会话 ID
    sender_id: str = ""                                                 # 发送者标识（用户 ID）
    channel_type: ChannelType = ChannelType.CONSOLE                     # 消息来源渠道
    content: str = ""                                                   # 消息文本内容
    timestamp: float = field(default_factory=time.time)                 # 消息发送时间戳
    metadata: Dict[str, Any] = field(default_factory=dict)              # 消息附加元数据
    media_urls: List[str] = field(default_factory=list)                 # 附件/媒体文件 URL 列表

    def to_event(self) -> Event:
        return Event(
            session_id=self.session_id,
            event_type=EventType.USER_MESSAGE,
            content={
                "message_id": self.message_id,
                "sender_id": self.sender_id,
                "channel_type": self.channel_type.value if isinstance(self.channel_type, Enum) else self.channel_type,
                "content": self.content,
                "media_urls": self.media_urls,
            },
            metadata=self.metadata,
        )


@dataclass
class ToolCall:
    """模型发起的工具调用请求"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))  # 工具调用唯一标识
    name: str = ""                                              # 工具名称
    arguments: Dict[str, Any] = field(default_factory=dict)     # 工具调用参数（JSON 格式）

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", ""),
            arguments=data.get("arguments", {}),
        )


@dataclass
class ToolResult:
    """工具执行后返回的结果"""
    tool_call_id: str          # 对应的工具调用 ID
    status: str                # 执行状态（如 success / failure）
    content: Any = ""          # 工具执行返回的数据
    error: Optional[str] = None  # 执行失败时的错误信息

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "content": self.content,
            "error": self.error,
        }


@dataclass
class ModelResponse:
    """大语言模型的完整响应"""
    content: Optional[str] = None                              # 模型生成的文本回复
    tool_calls: Optional[List[ToolCall]] = None                # 模型请求调用的工具列表
    stop_reason: StopReason = StopReason.END_TURN              # 模型停止生成的原因
    usage: Optional[Dict[str, Any]] = None                     # token 用量统计（如 prompt_tokens, completion_tokens）


@dataclass
class SessionMetadata:
    """会话元数据，记录会话的基本信息和状态"""
    session_id: str                                          # 会话唯一标识
    creator_id: str = ""                                     # 会话创建者 ID
    channel_type: ChannelType = ChannelType.CONSOLE          # 会话所属渠道
    tags: List[str] = field(default_factory=list)            # 会话标签列表，用于分类和检索
    created_at: float = field(default_factory=time.time)     # 会话创建时间戳
    status: str = "active"                                   # 会话状态（如 active / archived / completed）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "creator_id": self.creator_id,
            "channel_type": self.channel_type.value if isinstance(self.channel_type, Enum) else self.channel_type,
            "tags": self.tags,
            "created_at": self.created_at,
            "status": self.status,
        }
