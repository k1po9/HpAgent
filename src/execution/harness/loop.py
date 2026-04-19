from dataclasses import dataclass
from typing import Callable, Awaitable, Optional
import time
from .events import ExecutionEvent, EventType, StopReason
from ...core.config import LoopConfig
from ...model.client import ModelClient, ModelResponse
from ...tools.service import ToolService


class AgentLoop:
    def __init__(self, model_client: ModelClient, tool_service: ToolService, loop_config: LoopConfig):
        self.model_client = model_client
        self.tool_service = tool_service
        self.config = loop_config

    async def run(
        self,
        messages: list[dict],
        on_event: Optional[Callable[[ExecutionEvent], Awaitable[None]]] = None,
    ) -> tuple[str, list[ExecutionEvent]]:
    
        turn = 0
        events = []
        current_messages = messages.copy()

        if on_event:
            await on_event(ExecutionEvent(
                type=EventType.LOOP_STARTED,
                turn_index=0,
                timestamp=time.time(),
                data={"tools_count": len(self.tool_service.list_all_tools())}
            ))

        while turn < self.config.max_turns:
            turn += 1
            
            if on_event:
                await on_event(ExecutionEvent(
                    type=EventType.MODEL_CALLED,
                    turn_index=turn,
                    timestamp=time.time(),
                    data={"message_count": len(current_messages)}
                ))

            text_buffer = ""
            async def on_text_delta(delta: str):
                nonlocal text_buffer
                text_buffer += delta
                if on_event:
                    await on_event(ExecutionEvent(
                        type=EventType.TEXT_DELTA,
                        turn_index=turn,
                        timestamp=time.time(),
                        data={"content": delta}
                    ))

            response = await self.model_client.generate(
                messages=current_messages,
                tool_service=self.tool_service,
                stream=True,
                on_text_delta=on_text_delta,
            )
            
            current_messages.append(self._assistant_message(response))

            if response.stop_reason == StopReason.END_TURN:
                if on_event:
                    await on_event(ExecutionEvent(
                        type=EventType.TURN_COMPLETED,
                        turn_index=turn,
                        timestamp=time.time(),
                        data={"final": True}
                    ))
                
                if on_event:
                    await on_event(ExecutionEvent(
                        type=EventType.LOOP_COMPLETED,
                        turn_index=turn,
                        timestamp=time.time(),
                        data={"reason": "end_turn"}
                    ))
                
                return response.content or "", events
            elif response.stop_reason == StopReason.TOOL_USE and response.tool_calls:
                tool_results = await self.tool_service.route(
                    response.tool_calls, turn, on_event
                )
                current_messages.append({"role": "user", "content": tool_results})
                
                if on_event:
                    await on_event(ExecutionEvent(
                        type=EventType.TURN_COMPLETED,
                        turn_index=turn,
                        timestamp=time.time(),
                        data={"tool_calls_count": len(response.tool_calls)}
                    ))
            else:
                error_text = self._handle_error(response.stop_reason)
                if on_event:
                    await on_event(ExecutionEvent(
                        type=EventType.LOOP_COMPLETED,
                        turn_index=turn,
                        timestamp=time.time(),
                        data={"reason": "error", "error": str(response.stop_reason)}
                    ))
                return error_text, events

        if on_event:
            await on_event(ExecutionEvent(
                type=EventType.LOOP_COMPLETED,
                turn_index=turn,
                timestamp=time.time(),
                data={"reason": "max_turns"}
            ))
        
        return "[Max turns exceeded]", events

    def _assistant_message(self, response: ModelResponse) -> dict:
        content_parts = []
        if response.content:
            content_parts.append({"type": "text", "text": response.content})
        
        if response.tool_calls:
            for tc in response.tool_calls:
                content_parts.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
        
        return {"role": "assistant", "content": content_parts}

    def _handle_error(self, stop_reason: StopReason) -> str:
        if stop_reason == StopReason.MAX_TOKENS:
            return "[Response truncated: max tokens reached]"
        elif stop_reason == StopReason.REFUSAL:
            return "[Model refused to respond]"
        elif stop_reason == StopReason.ERROR:
            return "[An error occurred during execution]"
        else:
            return "[Unknown stop reason]"
