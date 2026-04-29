"""
AgentWorkflow — Temporal Workflow replacing Orchestrator + Harness wake loop.

This is the deterministic orchestration core. All non-deterministic operations
(model calls, tool execution, context building, channel I/O) are delegated to Activities.
"""
from datetime import timedelta
from typing import List, Dict, Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class AgentWorkflow:
    """
    Durable agentic-loop workflow.

    Replaces:
      - Orchestrator.receive_request()  → run() entrypoint
      - Orchestrator.process_session()  → main loop body
      - Harness.wake()                  → loop iteration: build context → call model → route tools

    Events are stored in-memory and automatically persisted by Temporal's event sourcing.
    External callers can query status/events or send signals (e.g. cancel).
    """

    def __init__(self):
        self._events: List[Dict[str, Any]] = []
        self._max_turns = 20
        self._turn_count = 0
        self._completed = False

    @workflow.run
    async def run(self, user_message: Dict[str, Any]) -> Dict[str, Any]:
        # Record the inbound user message
        self._events.append({
            "type": "USER_MESSAGE",
            "content": user_message.get("content", ""),
            "sender_id": user_message.get("sender_id", ""),
            "channel_type": user_message.get("channel_type", "console"),
            "timestamp": user_message.get("timestamp", 0),
        })

        final_content = ""

        while self._turn_count < self._max_turns and not self._completed:
            self._turn_count += 1

            # ── Build context ──
            channel_type = user_message.get("channel_type", "console")
            context = await workflow.execute_activity(
                "build_context_activity",
                args=[self._events, channel_type],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # ── Get available tools ──
            tools = await workflow.execute_activity(
                "get_available_tools_activity",
                args=[],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # ── Call model (with retry) ──
            model_response = await workflow.execute_activity(
                "call_model_activity",
                args=[context, tools],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=60),
                    maximum_attempts=3,
                ),
            )

            self._events.append({
                "type": "MODEL_MESSAGE",
                "content": model_response.get("content", ""),
                "tool_calls": model_response.get("tool_calls", []),
                "stop_reason": model_response.get("stop_reason", ""),
            })

            final_content = model_response.get("content", "")

            # ── Route tool calls ──
            tool_calls = model_response.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    result = await workflow.execute_activity(
                        "execute_tool_activity",
                        args=[tc.get("name", ""), tc.get("arguments", {})],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=1),
                            maximum_attempts=2,
                        ),
                    )
                    self._events.append({
                        "type": "TOOL_RESULT",
                        "tool_call_id": tc.get("id", ""),
                        "tool_name": tc.get("name", ""),
                        "result": result,
                    })
            else:
                break

        # ── Send final response through channel ──
        await workflow.execute_activity(
            "send_response_activity",
            args=[final_content, user_message],
            start_to_close_timeout=timedelta(seconds=10),
        )

        self._completed = True
        return {
            "status": "completed",
            "content": final_content,
            "turns": self._turn_count,
            "event_count": len(self._events),
        }

    @workflow.query
    def get_events(self) -> List[Dict[str, Any]]:
        """Return all session events collected so far."""
        return self._events

    @workflow.query
    def get_status(self) -> Dict[str, Any]:
        """Return execution status snapshot."""
        return {
            "turns": self._turn_count,
            "completed": self._completed,
            "event_count": len(self._events),
            "max_turns": self._max_turns,
        }

    @workflow.signal
    async def cancel_session(self) -> None:
        """Signal the workflow to terminate early at the next loop iteration."""
        self._completed = True
