"""Channel-neutral Brain/Action tool loop used by AgentExecutionFacade."""
from __future__ import annotations

import asyncio
from typing import Any

from actions.runtime import ActionRuntime
from brain.engine import BrainEngine

from .facade import (
    EventSink,
    ExecutionAuditSink,
    ExecutionControl,
    ExecutionRequest,
    ExecutionResult,
    StableExecutionFailure,
)


class DefaultBrainActionLoop:
    def __init__(
        self,
        brain: BrainEngine,
        actions: ActionRuntime,
        max_tool_turns: int = 20,
        cancel_cleanup_timeout_seconds: float = 30,
        cancel_poll_interval_seconds: float = 1,
    ):
        if cancel_cleanup_timeout_seconds <= 0:
            raise ValueError("cancel cleanup timeout must be positive")
        if cancel_poll_interval_seconds <= 0:
            raise ValueError("cancel poll interval must be positive")
        self._brain = brain
        self._actions = actions
        self._max_tool_turns = max_tool_turns
        self._cancel_cleanup_timeout_seconds = cancel_cleanup_timeout_seconds
        self._cancel_poll_interval_seconds = cancel_poll_interval_seconds

    async def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
        events: EventSink,
        audit: ExecutionAuditSink,
    ) -> ExecutionResult:
        self._actions.reset_turn(request.session_id, request.execution_id)
        messages: list[dict[str, Any]] = list(request.context)
        observations: list[dict[str, Any]] = [
            {"role": "user", "content": request.user_content}
        ]
        if request.context_provider is not None:
            try:
                recall_query, _hyde_context = await self._await_with_cancellation(
                    self._brain.rewrite_recall_query(
                        user_content=request.user_content,
                        group_context_text="",
                        sender_name="",
                    ),
                    control,
                )
                memories = await request.context_provider.recall_long_term(recall_query)
                messages = list(request.context_provider.compose(memories))
            except StableExecutionFailure:
                raise
            except Exception as exc:
                raise StableExecutionFailure("context_build_failed") from exc
        for turn in range(1, self._max_tool_turns + 1):
            await self._checkpoint(control, "selecting_tools")
            await events.progress("selecting_tools", "正在准备工具。")
            try:
                tools = await self._actions.select_tools(
                    user_content=request.user_content,
                    session_id=request.session_id,
                    execution_id=request.execution_id,
                )
            except Exception as exc:
                raise StableExecutionFailure("tool_failed") from exc
            await events.progress("generating", "正在生成回复。")
            try:
                decision = await self._await_with_cancellation(
                    self._brain.generate_chat_decision(
                        messages=messages,
                        tools=tools or None,
                        channel_overrides=dict(
                            (request.metadata or {}).get(
                                "channel_overrides", {}
                            )
                        ),
                    ),
                    control,
                )
            except TimeoutError as exc:
                raise StableExecutionFailure("model_timeout") from exc
            except Exception as exc:
                raise StableExecutionFailure("model_unavailable") from exc
            await self._audit_best_effort(
                audit.model_step(
                    request.execution_id,
                    turn,
                    decision.content,
                    tuple(
                        item.to_model_event_tool_call()
                        for item in getattr(decision, "action_requests", ())
                    ),
                    getattr(decision, "stop_reason", ""),
                    getattr(decision, "input_context", None),
                )
            )
            if not decision.has_actions:
                content = decision.content or "抱歉，我暂时无法处理这个消息，请稍后重试。"
                observations.append({"role": "assistant", "content": content})
                return ExecutionResult(content, turn, tuple(observations))
            tool_progress = getattr(events, "tool_progress", None)
            if tool_progress is not None:
                await tool_progress(
                    tuple(item.name for item in decision.action_requests)
                )
            messages.append({
                "role": "assistant", "content": decision.content,
                "tool_calls": [item.to_assistant_tool_call() for item in decision.action_requests],
            })
            observations.append({
                "role": "assistant",
                "content": decision.content,
                "tool_calls": [
                    item.to_assistant_tool_call()
                    for item in decision.action_requests
                ],
            })
            for action in decision.action_requests:
                await self._checkpoint(control, "executing_tool")
                await events.progress("executing_tool", f"正在执行 {action.name}。")
                side_effect_class = self._side_effect_class(
                    request.session_id, action.name
                )
                if side_effect_class not in {"none", "read_only"}:
                    try:
                        await audit.authorize_tool_intent(
                            request.execution_id,
                            action.id,
                            action.name,
                            side_effect_class,
                        )
                    except Exception as exc:
                        raise StableExecutionFailure(
                            "side_effect_audit_unavailable"
                        ) from exc
                try:
                    result = await self._execute_with_cancellation(
                        action, request, control
                    )
                except TimeoutError as exc:
                    raise StableExecutionFailure("tool_timeout") from exc
                except Exception as exc:
                    raise StableExecutionFailure("tool_failed") from exc
                await self._audit_best_effort(
                    audit.tool_result(
                        request.execution_id,
                        action.id,
                        action.name,
                        result,
                    )
                )
                messages.append({
                    "role": "tool", "tool_call_id": action.id, "name": action.name,
                    "content": result.display_result,
                })
        await self._checkpoint(control, "generating")
        await events.progress("generating", "正在整理结果。")
        try:
            decision = await self._await_with_cancellation(
                self._brain.generate_final_decision(
                    messages=messages,
                    channel_overrides=dict(
                        (request.metadata or {}).get("channel_overrides", {})
                    ),
                ),
                control,
            )
        except TimeoutError as exc:
            raise StableExecutionFailure("model_timeout") from exc
        except Exception as exc:
            raise StableExecutionFailure("model_unavailable") from exc
        await self._audit_best_effort(
            audit.model_step(
                request.execution_id,
                self._max_tool_turns + 1,
                decision.content,
                (),
                getattr(decision, "stop_reason", "forced_final"),
                getattr(decision, "input_context", None),
            )
        )
        content = decision.content or "抱歉，我暂时无法处理这个消息，请稍后重试。"
        observations.append({"role": "assistant", "content": content})
        return ExecutionResult(
            content,
            self._max_tool_turns,
            tuple(observations),
        )

    @staticmethod
    async def _audit_best_effort(awaitable: Any) -> None:
        """Ordinary execution observations degrade instead of failing a Run."""
        try:
            await awaitable
        except Exception:
            return

    def _side_effect_class(self, session_id: str, tool_name: str) -> str:
        classify = getattr(self._actions, "side_effect_class", None)
        if classify is None:
            # Compatibility fakes/legacy runtimes predate metadata. Production
            # ActionRuntime always supplies the trusted registry classification.
            return "read_only"
        return str(classify(session_id, tool_name))

    @staticmethod
    def _raise_if_cancelled(control: ExecutionControl) -> None:
        raise_if_cancelled = getattr(control, "raise_if_cancelled", None)
        if raise_if_cancelled is not None:
            raise_if_cancelled()
        elif control.cancelled():
            raise asyncio.CancelledError

    @classmethod
    async def _checkpoint(cls, control: ExecutionControl, phase: str) -> None:
        cls._raise_if_cancelled(control)
        heartbeat = getattr(control, "heartbeat", None)
        if heartbeat is not None:
            await heartbeat(phase)

    async def _execute_with_cancellation(self, action: Any, request: ExecutionRequest, control: ExecutionControl):
        """Poll a running tool so an Activity cancellation is observed promptly."""
        task = asyncio.create_task(self._actions.execute_request(
            action,
            session_id=request.session_id,
            execution_id=request.execution_id,
            user_query=request.user_content,
        ))
        try:
            while not task.done():
                self._raise_if_cancelled(control)
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self._cancel_poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
            return await task
        except asyncio.CancelledError:
            await self._cancel_with_budget(task)
            raise

    async def _await_with_cancellation(self, awaitable: Any, control: ExecutionControl):
        task = asyncio.create_task(awaitable)
        try:
            while not task.done():
                self._raise_if_cancelled(control)
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self._cancel_poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
            return await task
        except asyncio.CancelledError:
            await self._cancel_with_budget(task)
            raise

    async def _cancel_with_budget(self, task: asyncio.Task) -> None:
        task.cancel()
        done, _pending = await asyncio.wait(
            {task}, timeout=self._cancel_cleanup_timeout_seconds
        )
        if done:
            await asyncio.gather(task, return_exceptions=True)
            return
        # A cancellation-hostile SDK/thread must not hold the Activity open.
        # Its eventual result is detached and ignored; Web Host closes Events
        # and terminal persistence rechecks the authoritative Run state.
        task.add_done_callback(self._consume_detached_result)

    @staticmethod
    def _consume_detached_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except BaseException:
            pass
