from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    ERROR = "error"

def map_openai_finish_reason(oa_reason: str) -> StopReason:
    mapping = {
        "stop": StopReason.END_TURN,
        "tool_calls": StopReason.TOOL_USE,
        "function_call": StopReason.TOOL_USE,
        "length": StopReason.MAX_TOKENS,
        "content_filter": StopReason.REFUSAL,
        # 其他/异常统一归为 error
        None: StopReason.ERROR,
        "": StopReason.ERROR,
    }
    return mapping.get(oa_reason, StopReason.ERROR)

class EventType(str, Enum):
    LOOP_STARTED = "loop_started"
    LOOP_COMPLETED = "loop_completed"
    MODEL_CALLED = "model_called"
    TEXT_DELTA = "text_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TURN_COMPLETED = "turn_completed"
    ERROR = "error"


@dataclass
class ExecutionEvent:
    type: EventType
    turn_index: int
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)

