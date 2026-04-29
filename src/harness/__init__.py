"""
Harness — the stateless brain layer.

Provides:
  - Activities:     Temporal Activities wrapping non-deterministic operations
                    (model calls, tool execution, context building, channel I/O)
  - ContextBuilder: Assembles LLM messages list from event history + channel identity
"""
from .activities import (
    inject,
    build_context_activity,
    get_available_tools_activity,
    call_model_activity,
    execute_tool_activity,
    send_response_activity,
)
from .context_builder import HarnessContextBuilder

__all__ = [
    "inject",
    "build_context_activity",
    "get_available_tools_activity",
    "call_model_activity",
    "execute_tool_activity",
    "send_response_activity",
    "HarnessContextBuilder",
]
