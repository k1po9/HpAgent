"""
OrchestrationWorkflow — the conductor's Temporal Workflow.

This is the deterministic orchestration core. The Orchestrator coordinates Harness
(the brain), Session (the memory), and Sandbox (the hands) through the agentic loop.

The workflow is long-running: it processes an initial message, then waits for
subsequent messages via the `new_message` signal. This enables cross-client memory
sharing — QQ and Web messages route to the same workflow via account_id.

All non-deterministic operations (model calls, tool execution, context building,
channel I/O) are delegated to Activities — the Harness's decomposed brain operations.
"""
from datetime import timedelta
from typing import List, Dict, Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class OrchestrationWorkflow:
    """
    Durable orchestration workflow for the agentic loop.

    Supports cross-client memory: events from multiple channels (QQ, Web)
    are stored in the same self._events list, shared via account-level workflow_id.
    """

    def __init__(self):
        self._events: List[Dict[str, Any]] = []
        self._max_turns = 20
        self._total_turns = 0
        self._completed = False
        self._account_id = ""
        self._session_id = ""
        self._pending_messages: List[Dict[str, Any]] = []

    @workflow.run
    async def run(self, user_message: Dict[str, Any]) -> Dict[str, Any]:
        self._account_id = user_message.get("account_id", "")
        self._session_id = user_message.get("session_id", "")

        self._events.append({
            "type": "USER_MESSAGE",
            "content": user_message.get("content", ""),
            "sender_id": user_message.get("sender_id", ""),
            "channel_type": user_message.get("channel_type", "console"),
            "account_id": self._account_id,
            "session_id": self._session_id,
            "timestamp": user_message.get("timestamp", 0),
        })

        final_content = await self._process_turn(user_message)

        # Main loop — wait for new messages via signal, then process each
        while True:
            await workflow.wait_condition(lambda: bool(self._pending_messages) or self._completed)
            if self._completed:
                break
            if self._pending_messages:
                next_msg = self._pending_messages.pop(0)
                final_content = await self._process_turn(next_msg)

        return {
            "status": "completed",
            "content": final_content,
            "turns": self._total_turns,
            "event_count": len(self._events),
            "account_id": self._account_id,
            "session_id": self._session_id,
        }

    async def _process_turn(self, user_message: Dict[str, Any]) -> str:
        """Run one full agentic loop turn: context → model → tools → response."""
        final_content = ""
        turn_completed = False
        msg_turns = 0

        while msg_turns < self._max_turns and not turn_completed and not self._completed:
            msg_turns += 1
            self._total_turns += 1
            channel_type = user_message.get("channel_type", "console")

            # ── Harness: build context (brain assembles memory) ──
            context = await workflow.execute_activity(
                "build_context_activity",
                args=[self._events, channel_type],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # ── Harness: inventory available tools (brain inspects hands) ──
            tools = await workflow.execute_activity(
                "get_available_tools_activity",
                args=[],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # ── Harness: call model (brain invokes LLM) ──
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

            # ── Harness: execute tool calls (brain directs hands) ──
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
                turn_completed = True

        # ── Harness: deliver response (brain speaks through channel) ──
        await workflow.execute_activity(
            "send_response_activity",
            args=[final_content, user_message],
            start_to_close_timeout=timedelta(seconds=10),
        )

        return final_content

    @workflow.signal
    async def new_message(self, user_message: Dict[str, Any]) -> None:
        """Enqueue an incoming user message for processing."""
        self._events.append({
            "type": "USER_MESSAGE",
            "content": user_message.get("content", ""),
            "sender_id": user_message.get("sender_id", ""),
            "channel_type": user_message.get("channel_type", "console"),
            "account_id": user_message.get("account_id", self._account_id),
            "session_id": user_message.get("session_id", self._session_id),
            "timestamp": user_message.get("timestamp", 0),
        })
        self._pending_messages.append(user_message)

    @workflow.signal
    async def cancel_session(self) -> None:
        """Signal the workflow to terminate early."""
        self._completed = True

    @workflow.query
    def get_events(self) -> List[Dict[str, Any]]:
        """Return all session events collected so far."""
        return self._events

    @workflow.query
    def get_status(self) -> Dict[str, Any]:
        """Return execution status snapshot."""
        return {
            "turns": self._total_turns,
            "completed": self._completed,
            "event_count": len(self._events),
            "max_turns": self._max_turns,
            "account_id": self._account_id,
            "session_id": self._session_id,
        }
