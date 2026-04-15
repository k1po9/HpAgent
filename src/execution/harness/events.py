from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    ERROR = "error"


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
