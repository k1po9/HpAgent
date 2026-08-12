"""Channel-neutral Brain/Action tool loop used by AgentExecutionFacade."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from actions.runtime import ActionRuntime
from brain.engine import BrainEngine
from common.logging import log_event

from .facade import (
    EventSink,
    ExecutionAuditSink,
    ExecutionControl,
    ExecutionRequest,
    ExecutionResult,
    StableExecutionFailure,
)

logger = logging.getLogger("HpAgent.BrainActionLoop")


class ToolTimeoutCapExpired(TimeoutError):
    """A per-call tool timeout cap expired while the tool was in flight.

    The Activity's own start-to-close deadline and a per-tool call cap are
    distinct: the former is a whole-Run timeout (``run_timeout``) while the
    latter is a single-tool timeout (``tool_timeout``).  This subclass lets the
    caller tell them apart without guessing from timing.
    """


class DefaultBrainActionLoop:
    def __init__(
        self,
        brain: BrainEngine,
        actions: ActionRuntime,
        max_tool_turns: int = 20,
        cancel_cleanup_timeout_seconds: float = 30,
        cancel_poll_interval_seconds: float = 1,
        deadline_margin_seconds: float = 1.0,
        tool_timeout_seconds: float | None = 120.0,
    ):
        if cancel_cleanup_timeout_seconds <= 0:
            raise ValueError("cancel cleanup timeout must be positive")
        if cancel_poll_interval_seconds <= 0:
            raise ValueError("cancel poll interval must be positive")
        if deadline_margin_seconds < 0:
            raise ValueError("deadline margin must be non-negative")
        if tool_timeout_seconds is not None and tool_timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        self._brain = brain
        self._actions = actions
        self._max_tool_turns = max_tool_turns
        self._cancel_cleanup_timeout_seconds = cancel_cleanup_timeout_seconds
        self._cancel_poll_interval_seconds = cancel_poll_interval_seconds
        # The loop must stop slightly before the Activity deadline so a small
        # scheduling slip cannot let Temporal's own start-to-close timeout fire
        # and misclassify the failure as an internal execution error.
        self._deadline_margin_seconds = deadline_margin_seconds
        # Design §7 default for a single ordinary tool call (unfrozen).  A cap
        # is what makes ``tool_timeout`` reachable independently of the whole
        # Run deadline; passing None disables the per-tool cap.
        self._tool_timeout_seconds = tool_timeout_seconds

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
                self._raise_if_expired(control)
                recall_query, _hyde_context = await self._await_with_control(
                    self._brain.rewrite_recall_query(
                        user_content=request.user_content,
                        group_context_text=request.group_context_text,
                        sender_name=request.sender_name,
                    ),
                    control,
                )
                self._raise_if_expired(control)
                memories = await self._await_with_control(
                    request.context_provider.recall_long_term(recall_query),
                    control,
                )
                self._raise_if_expired(control)
                messages = list(request.context_provider.compose(memories))
            except TimeoutError as exc:
                # The whole Run exceeded its deadline while assembling context.
                raise StableExecutionFailure("run_timeout") from exc
            except StableExecutionFailure:
                raise
            except Exception as exc:
                raise StableExecutionFailure("context_build_failed") from exc
        for turn in range(1, self._max_tool_turns + 1):
            await self._checkpoint(control, "selecting_tools")
            await events.progress("selecting_tools", "正在准备工具。")
            try:
                tools = await self._await_with_control(
                    self._actions.select_tools(
                        user_content=request.user_content,
                        session_id=request.session_id,
                        execution_id=request.execution_id,
                    ),
                    control,
                )
            except TimeoutError as exc:
                raise StableExecutionFailure("run_timeout") from exc
            except StableExecutionFailure:
                raise
            except Exception as exc:
                raise StableExecutionFailure("tool_failed") from exc
            await events.progress("calling_model", "正在生成回复。")
            model_started_at = time.monotonic()
            log_event(logger, logging.INFO, "model_call_started", "model", **self._correlation(request),
                      turn=turn, phase="agent_turn", status="started")
            try:
                decision = await self._await_with_control(
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
                log_event(logger, logging.ERROR, "model_call_failed", "model", **self._correlation(request),
                          turn=turn, phase="agent_turn", status="failed", elapsed_ms=round((time.monotonic() - model_started_at) * 1000), error_code="model_timeout")
                raise StableExecutionFailure("model_timeout") from exc
            except StableExecutionFailure:
                raise
            except Exception as exc:
                log_event(logger, logging.ERROR, "model_call_failed", "model", **self._correlation(request),
                          turn=turn, phase="agent_turn", status="failed", elapsed_ms=round((time.monotonic() - model_started_at) * 1000), error_code="model_unavailable")
                raise StableExecutionFailure("model_unavailable") from exc
            log_event(logger, logging.INFO, "model_call_completed", "model", **self._correlation(request),
                      turn=turn, phase="agent_turn", status="success", elapsed_ms=round((time.monotonic() - model_started_at) * 1000),
                      tool_count=len(getattr(decision, "action_requests", ())), stop_reason=getattr(decision, "stop_reason", ""))
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
                content = self._safe_final_content(decision.content)
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
                    tool_started_at = time.monotonic()
                    log_event(logger, logging.INFO, "tool_execution_started", "tool", **self._correlation(request),
                              turn=turn, tool_call_id=action.id, tool=action.name, status="started")
                    result = await self._execute_with_cancellation(
                        action,
                        request,
                        control,
                        timeout_cap=self._tool_timeout_seconds,
                    )
                except ToolTimeoutCapExpired as exc:
                    # The single-tool call cap expired: this tool timed out,
                    # not the whole Run.
                    log_event(logger, logging.ERROR, "tool_execution_failed", "tool", **self._correlation(request),
                              turn=turn, tool_call_id=action.id, tool=action.name, status="failed", elapsed_ms=round((time.monotonic() - tool_started_at) * 1000), error_code="tool_timeout")
                    raise StableExecutionFailure("tool_timeout") from exc
                except TimeoutError as exc:
                    # The Activity's global deadline expired while the tool was
                    # in flight: the whole Run timed out, so cancellation must
                    # not leak into the Workflow as an unexpected cancel.
                    log_event(logger, logging.ERROR, "tool_execution_failed", "tool", **self._correlation(request),
                              turn=turn, tool_call_id=action.id, tool=action.name, status="failed", elapsed_ms=round((time.monotonic() - tool_started_at) * 1000), error_code="run_timeout")
                    raise StableExecutionFailure("run_timeout") from exc
                except StableExecutionFailure:
                    raise
                except Exception as exc:
                    log_event(logger, logging.ERROR, "tool_execution_failed", "tool", **self._correlation(request),
                              turn=turn, tool_call_id=action.id, tool=action.name, status="failed", elapsed_ms=round((time.monotonic() - tool_started_at) * 1000), error_code="tool_failed")
                    raise StableExecutionFailure("tool_failed") from exc
                log_event(logger, logging.INFO, "tool_execution_completed", "tool", **self._correlation(request),
                          turn=turn, tool_call_id=action.id, tool=action.name, status="success", elapsed_ms=round((time.monotonic() - tool_started_at) * 1000))
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
        await events.progress("finalizing", "正在整理结果。")
        final_turn = self._max_tool_turns + 1
        model_started_at = time.monotonic()
        log_event(
            logger,
            logging.INFO,
            "model_call_started",
            "model",
            **self._correlation(request),
            turn=final_turn,
            phase="forced_final",
            status="started",
        )
        try:
            decision = await self._await_with_control(
                self._brain.generate_final_decision(
                    messages=messages,
                    channel_overrides=dict(
                        (request.metadata or {}).get("channel_overrides", {})
                    ),
                ),
                control,
            )
        except TimeoutError as exc:
            log_event(
                logger,
                logging.ERROR,
                "model_call_failed",
                "model",
                **self._correlation(request),
                turn=final_turn,
                phase="forced_final",
                status="failed",
                elapsed_ms=round((time.monotonic() - model_started_at) * 1000),
                error_code="model_timeout",
            )
            raise StableExecutionFailure("model_timeout") from exc
        except StableExecutionFailure:
            raise
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "model_call_failed",
                "model",
                **self._correlation(request),
                turn=final_turn,
                phase="forced_final",
                status="failed",
                elapsed_ms=round((time.monotonic() - model_started_at) * 1000),
                error_code="model_unavailable",
            )
            raise StableExecutionFailure("model_unavailable") from exc
        log_event(
            logger,
            logging.INFO,
            "model_call_completed",
            "model",
            **self._correlation(request),
            turn=final_turn,
            phase="forced_final",
            status="success",
            elapsed_ms=round((time.monotonic() - model_started_at) * 1000),
            tool_count=0,
            stop_reason=getattr(decision, "stop_reason", "forced_final"),
        )
        await self._audit_best_effort(
            audit.model_step(
                request.execution_id,
                final_turn,
                decision.content,
                (),
                getattr(decision, "stop_reason", "forced_final"),
                getattr(decision, "input_context", None),
            )
        )
        content = self._safe_final_content(decision.content)
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

    @staticmethod
    def _correlation(request: ExecutionRequest) -> dict[str, Any]:
        metadata = request.metadata or {}
        return {
            "run_id": metadata.get("run_id"),
            "execution_id": request.execution_id,
            "conversation_id": request.conversation_id or None,
            "session_id": request.session_id or None,
            "account_id": request.account_id or None,
            "surface": metadata.get("surface"),
        }

    @staticmethod
    def _safe_final_content(content: str | None) -> str:
        """Prevent XML fallback tool syntax from leaking to either surface."""
        value = content or ""
        if "<tool_call>" in value:
            value = re.sub(r"<tool_call>.*?</tool_call>", "", value, flags=re.DOTALL).strip()
        return value or "抱歉，我暂时无法处理这个消息，请稍后重试。"

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

    @staticmethod
    def _remaining_seconds(control: ExecutionControl) -> float:
        """Seconds until the execution deadline; infinite when there is none.

        Fakes that predate the deadline never expose ``deadline``, and the QQ
        legacy chain reports ``datetime.max`` (an explicit no-op), so a missing
        or effectively-infinite deadline must not trip the run-timeout paths.
        """
        deadline = getattr(control, "deadline", None)
        if deadline is None:
            return float("inf")
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        return remaining if remaining > 0 else 0.0

    def _remaining_with_margin(self, control: ExecutionControl) -> float:
        """Seconds left once the safety margin has been reserved.

        The margin guarantees the loop stops before Temporal's own
        start-to-close timeout can fire and misclassify the failure.
        """
        remaining = self._remaining_seconds(control)
        if remaining == float("inf"):
            return float("inf")
        return max(0.0, remaining - self._deadline_margin_seconds)

    def _raise_if_expired(self, control: ExecutionControl) -> None:
        """Fail the whole Run before starting work close to the deadline."""
        if self._remaining_with_margin(control) <= 0:
            raise StableExecutionFailure("run_timeout")

    async def _checkpoint(self, control: ExecutionControl, phase: str) -> None:
        self._raise_if_cancelled(control)
        self._raise_if_expired(control)
        heartbeat = getattr(control, "heartbeat", None)
        if heartbeat is not None:
            await heartbeat(phase)

    async def _execute_with_cancellation(
        self,
        action: Any,
        request: ExecutionRequest,
        control: ExecutionControl,
        *,
        timeout_cap: float | None = None,
    ):
        """Poll a running tool so cancellation/deadline are observed promptly.

        Cancellation always wins.  A global-deadline expiry raises plain
        ``TimeoutError`` (the whole Run timed out) while a per-tool ``timeout_cap``
        expiry raises :class:`ToolTimeoutCapExpired` (only this tool timed out),
        so the caller can map the two stable codes differently.  The in-flight
        tool is always cancelled within the cleanup budget first.
        """
        task = asyncio.create_task(self._actions.execute_request(
            action,
            session_id=request.session_id,
            execution_id=request.execution_id,
            user_query=request.user_content,
        ))
        cap_deadline = (
            datetime.now(UTC) + timedelta(seconds=timeout_cap)
            if timeout_cap is not None
            else None
        )
        try:
            while not task.done():
                self._raise_if_cancelled(control)
                if cap_deadline is not None and datetime.now(UTC) >= cap_deadline:
                    await self._cancel_with_budget(task)
                    raise ToolTimeoutCapExpired("single-tool timeout cap expired")
                remaining = self._remaining_with_margin(control)
                if remaining <= 0:
                    # The tool ran past the Activity deadline.  Stop it within
                    # the cleanup budget and report the whole-Run timeout —
                    # never CancelledError, which the Workflow would treat as
                    # an unexpected cancellation.
                    await self._cancel_with_budget(task)
                    raise TimeoutError("execution deadline expired while tool in flight")
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=min(
                            self._cancel_poll_interval_seconds,
                            remaining,
                        ),
                    )
                except TimeoutError:
                    continue
            return await task
        except asyncio.CancelledError:
            await self._cancel_with_budget(task)
            raise

    async def _await_with_control(
        self,
        awaitable: Any,
        control: ExecutionControl,
        *,
        timeout_cap: float | None = None,
    ):
        """Await while honoring both cancellation and the execution deadline.

        Cancellation always wins (checked first). If the deadline — or an
        optional per-call cap — expires while the awaitable is in flight, the
        awaitable is cancelled within the cleanup budget and TimeoutError is
        raised so the caller maps a stable timeout code. A result that lands
        after the budget is detached and ignored.

        The cap is resolved to an absolute deadline once, before the loop, so
        it actually expires instead of being re-reset to its full value on
        every poll iteration.
        """
        task = asyncio.create_task(awaitable)
        cap_deadline = (
            datetime.now(UTC) + timedelta(seconds=timeout_cap)
            if timeout_cap is not None
            else None
        )
        try:
            while not task.done():
                self._raise_if_cancelled(control)
                remaining = self._remaining_with_margin(control)
                if cap_deadline is not None:
                    cap_remaining = (cap_deadline - datetime.now(UTC)).total_seconds()
                    remaining = min(remaining, max(0.0, cap_remaining))
                if remaining <= 0:
                    await self._cancel_with_budget(task)
                    raise TimeoutError
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=min(self._cancel_poll_interval_seconds, remaining),
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
