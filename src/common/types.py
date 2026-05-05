"""
核心数据类型 —— 系统中流转的所有 dataclass 和枚举。

数据流转路径（简化）：
  OneBot JSON → normalize_message() → UnifiedMessage → worker 解构为 dict
  → Temporal Workflow 入参 → self._events[] → ContextBuilder.build()
  → LLM messages[] → ModelResponse → 写回 self._events[]
  → send_response_activity → ChannelRouter.send(UnifiedMessage) → 渠道输出

枚举类型：
  - EventType: 事件类型（USER_MESSAGE / MODEL_MESSAGE / TOOL_CALL / TOOL_RESULT / ...）
  - ChannelType: 渠道类型（NAPCAT / WEB / CONSOLE）
  - StopReason: 模型停止生成原因（END_TURN / TOOL_USE / MAX_TOKENS / REFUSAL / ERROR）
  - ErrorSeverity: 错误严重级别（RECOVERABLE / FATAL）

数据类：
  - Event: 系统内部通用事件（在 Workflow 和 ContextBuilder 之间传递）
  - UnifiedMessage: 统一消息格式（渠道入口的标准化格式，v5 新增 account_id）
  - ToolCall / ToolResult: 工具调用和结果
  - ModelResponse: 模型完整响应（content + tool_calls + stop_reason + usage）
  - SessionMetadata: 会话元信息
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import uuid
import time


# ═══════════════════════════════════════════════════════════════════════════════
# 枚举类型
# ═══════════════════════════════════════════════════════════════════════════════

class EventType(str, Enum):
    """事件类型枚举 —— 标识事件在对话流中的角色。

    每个事件按类型在 ContextBuilder 中被转换为对应的 LLM role：
      USER_MESSAGE   → role="user"
      MODEL_MESSAGE  → role="assistant"
      TOOL_RESULT    → role="user" (tool_result)
      TOOL_CALL      → 不直接入 messages，包含在 MODEL_MESSAGE 的 tool_calls 中
      其他           → 不进入 messages，仅用于状态管理
    """
    USER_MESSAGE = "user_message"            # 用户发送的消息
    MODEL_MESSAGE = "model_message"          # 模型生成的回复（可能含 tool_calls）
    TOOL_CALL = "tool_call"                  # 模型请求调用工具（中间事件）
    TOOL_RESULT = "tool_result"              # 工具执行返回的结果
    ERROR = "error"                          # 系统错误事件
    CONFIG_CHANGE = "config_change"          # 配置变更事件
    SESSION_START = "session_start"          # 会话开始标记
    SESSION_COMPLETE = "session_complete"    # 会话正常结束标记
    SESSION_ARCHIVED = "session_archived"    # 会话被归档标记
    LOOP_STARTED = "loop_started"            # 推理循环开始（Temporal Activity 边界）
    LOOP_COMPLETED = "loop_completed"        # 推理循环完成
    TURN_COMPLETED = "turn_completed"        # 单轮交互完成（一次 user → model → tool → response）


class ChannelType(str, Enum):
    """消息渠道类型枚举 —— 决定系统 prompt 身份和响应路由。

    每个渠道在 ContextBuilder 中有独立的身份声明和风格引导。
    ChannelRouter 根据此枚举值将响应路由到正确的渠道实现。
    """
    NAPCAT = "napcat"       # NapCat/OneBot v11 协议 → QQ 聊天
    WEB = "web"             # Web 页面 → 浏览器端
    CONSOLE = "console"     # 控制台终端 → CLI


class StopReason(str, Enum):
    """模型停止生成的原因枚举 —— 决定 agentic loop 是否继续。

    TOOL_USE → 继续循环（执行工具 → 再次调用模型）
    END_TURN / MAX_TOKENS / REFUSAL / ERROR → 结束本轮
    """
    END_TURN = "end_turn"       # 模型主动结束回复（自然终止）
    TOOL_USE = "tool_use"       # 模型请求调用工具，暂停生成等待工具结果
    MAX_TOKENS = "max_tokens"   # 达到最大 token 限制（回复被截断）
    REFUSAL = "refusal"         # 模型拒绝回答（安全策略触发）
    ERROR = "error"             # 生成过程发生错误（API 异常）


class ErrorSeverity(str, Enum):
    """错误严重级别 —— 决定编排层是否终止 Workflow。"""
    RECOVERABLE = "recoverable"  # 可恢复错误，系统可继续运行（如重试）
    FATAL = "fatal"              # 致命错误，Workflow 终止


# ═══════════════════════════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Event:
    """系统内部通用事件 —— 在 Temporal Workflow 和 ContextBuilder 间传递。

    一条 Event 可以代表用户消息、模型回复、工具调用/结果、错误等。
    content 和 metadata 使用 Dict 而非强类型字段，保证最大灵活性。

    Attributes:
        event_id: 事件唯一 ID（默认 UUID4）。
        session_id: 所属会话 ID。
        timestamp: Unix epoch float 时间戳。
        event_type: 事件类型枚举。
        content: 事件核心数据字典，结构随 event_type 而变化：
          - USER_MESSAGE: {"content": str, "sender_id": str, "channel_type": str}
          - MODEL_MESSAGE: {"text": str, "tool_calls": [...]}
          - TOOL_RESULT: {"tool_call_id": str, "result": Any, "error": str|None}
        metadata: 附加元数据（如 channel 特有字段 post_type、group_id 等）。
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)
    event_type: EventType = EventType.USER_MESSAGE
    content: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于 Temporal Workflow 存储和事件历史传输）。

        Temporal 要求所有 Workflow 参数和返回值可 JSON 序列化，
        因此 Enum 在此处转为 .value 字符串。
        """
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
        """从字典反序列化（从 Temporal Workflow 事件历史还原）。

        自动将字符串 event_type 转换回 EventType 枚举。
        """
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
    """统一消息格式 —— 将不同渠道的消息标准化为统一结构。

    这是所有渠道消息进入系统后的"通用语"(lingua franca)。
    无论来自 QQ (NapCat)、Web 还是 CLI，经过 normalize_message()
    后都变成 UnifiedMessage，后续流程不再关心原始渠道格式。

    Attributes:
        message_id: 消息唯一 ID。
        session_id: 所属会话 ID（worker 填充，渠道层初始为空）。
        account_id: 统一用户账号 ID（v5 新增，由 worker 填充）。
        sender_id: 渠道侧发送者标识（QQ 号、Web 用户 ID 等）。
        channel_type: 消息来源渠道。
        content: 文本内容。
        timestamp: 发送时间戳。
        metadata: 渠道特定元数据（OneBot 的 post_type、detail_type、group_id 等）。
        media_urls: 附件 URL 列表（图片、文件等）。
    """
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    account_id: str = ""
    sender_id: str = ""
    channel_type: ChannelType = ChannelType.CONSOLE
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    media_urls: List[str] = field(default_factory=list)

    def to_event(self) -> Event:
        """转换为 Event（用于写入事件历史）。

        content 字段展开为包含 message_id / account_id / sender_id / channel_type
        的结构化 dict，metadata 原样传递。
        """
        return Event(
            session_id=self.session_id,
            event_type=EventType.USER_MESSAGE,
            content={
                "message_id": self.message_id,
                "account_id": self.account_id,
                "sender_id": self.sender_id,
                "channel_type": self.channel_type.value if isinstance(self.channel_type, Enum) else self.channel_type,
                "content": self.content,
                "media_urls": self.media_urls,
            },
            metadata=self.metadata,
        )


@dataclass
class ToolCall:
    """模型发起的工具调用请求。

    对应 Anthropic API 的 tool_use content block 或 OpenAI API 的 function_call。

    Attributes:
        id: 工具调用唯一 ID（对应 API 返回的 tool_use id）。
        name: 工具名称（如 "calculator"、"web_search"）。
        arguments: 工具调用参数（已从 JSON 字符串解析为 dict）。
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)

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
    """工具执行后返回的结果。

    Attributes:
        tool_call_id: 对应的 ToolCall.id，用于关联请求和结果。
        status: 执行状态字符串（"success" / "error"）。
        content: 执行成功时的返回数据。
        error: 执行失败时的错误信息。
    """
    tool_call_id: str
    status: str
    content: Any = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "content": self.content,
            "error": self.error,
        }


@dataclass
class ModelResponse:
    """大语言模型的完整响应 —— 统一 OpenAI / Anthropic 格式。

    Attributes:
        content: 模型生成的文本（可能为 None，如纯 tool_use 响应）。
        tool_calls: 模型请求调用的工具列表（None 表示纯文本响应）。
        stop_reason: 模型停止原因。
        usage: token 用量统计 {"prompt_tokens": int, "completion_tokens": int, ...}。
    """
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    stop_reason: StopReason = StopReason.END_TURN
    usage: Optional[Dict[str, Any]] = None


@dataclass
class SessionMetadata:
    """会话元数据 —— 记录会话的基本信息和当前状态。

    用于 SessionManager.list_sessions() 的返回值，
    以及 Temporal Workflow 的 get_status() Query。
    """
    session_id: str
    account_id: str = ""
    creator_id: str = ""
    channel_type: ChannelType = ChannelType.CONSOLE
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "account_id": self.account_id,
            "creator_id": self.creator_id,
            "channel_type": self.channel_type.value if isinstance(self.channel_type, Enum) else self.channel_type,
            "tags": self.tags,
            "created_at": self.created_at,
            "status": self.status,
        }
