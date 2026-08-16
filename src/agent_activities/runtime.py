"""Model/tool/context capability Activities for durable Agent workflows."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, cast

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent.protocol import ActionRequest
from agent_execution.facade import StableExecutionFailure
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    CompactToolCall,
    ContextBootstrapInput,
    ContextBootstrapResult,
    FinalizeResultInput,
    ModelDecisionInput,
    ModelDecisionResult,
    PlanEvaluationInput,
    PlanEvaluationResult,
    PlanningInput,
    PlanningResult,
    PlanStep,
    ToolExecutionInput,
    ToolExecutionResult,
)
from common.logging import log_event

from .store import AgentDataStore, StaleFencingToken, TranscriptVersionConflict

context_logger = logging.getLogger("HpAgent.AgentContextActivity")
model_logger = logging.getLogger("HpAgent.ModelDecisionActivity")
tool_logger = logging.getLogger("HpAgent.ToolExecutionActivity")
plan_logger = logging.getLogger("HpAgent.PlanningActivity")


class DurableAgentActivities:
    """Dependency-injected Activity collection; instances never cross History."""

    def __init__(
        self,
        *,
        store: AgentDataStore,
        loader: Any,
        brain: Any,
        actions: Any,
        event_factory: Any,
        resource_prep: Any,
        lifecycle: Any,
    ) -> None:
        self.store = store
        self.loader = loader
        self.brain = brain
        self.actions = actions
        self.event_factory = event_factory
        self.resource_prep = resource_prep
        self.lifecycle = lifecycle

    @staticmethod
    def _attempt() -> int:
        try:
            return int(activity.info().attempt)
        except RuntimeError:
            return 1

    @staticmethod
    def _heartbeat(**details: object) -> None:
        try:
            activity.heartbeat({"schema_version": 1, **details})
        except RuntimeError:
            pass

    async def _tool_heartbeat_loop(self, request: ToolExecutionInput) -> None:
        while True:
            self._heartbeat(
                run_id=request.run_id,
                activity_kind="tool_execution",
                phase="executing",
                turn=request.turn,
                operation_id=request.operation_id,
                tool=request.tool_call.name,
                step_id=request.step_id,
            )
            await asyncio.sleep(15)

    @staticmethod
    def _check_schema(value: int) -> None:
        if value != AGENT_SCHEMA_VERSION:
            raise ApplicationError("unsupported Agent Activity schema", non_retryable=True)

    @staticmethod
    def _correlation(request: Any) -> dict[str, Any]:
        return {
            "run_id": request.run_id,
            "execution_id": request.run_id,
            "account_id": getattr(request, "account_id", None),
            "conversation_id": getattr(request, "conversation_id", None),
            "session_id": getattr(request, "session_id", None),
            "surface": "web",
            "strategy": getattr(request, "strategy", None),
            "plan_id": getattr(request, "plan_id", None),
            "plan_version": getattr(request, "plan_version", None),
            "step_id": getattr(request, "step_id", None),
            "turn": getattr(request, "turn", None),
            "operation_id": getattr(request, "operation_id", None),
            "activity_attempt": DurableAgentActivities._attempt(),
        }

    @staticmethod
    def _decision_from_payload(payload: dict[str, Any]) -> ModelDecisionResult:
        return ModelDecisionResult(
            schema_version=int(payload["schema_version"]),
            operation_id=str(payload["operation_id"]),
            decision_type=cast(Any, str(payload["decision_type"])),
            decision_ref=str(payload["decision_ref"]),
            tool_calls=tuple(CompactToolCall(**item) for item in payload.get("tool_calls", [])),
            transcript_version=int(payload["transcript_version"]),
            stop_reason=str(payload.get("stop_reason", "")),
            display_summary=str(payload.get("display_summary", "")),
        )

    @asynccontextmanager
    async def _workspace(self, request: Any):
        from agent_execution.web_adapters import TemporalActivityControl

        control = TemporalActivityControl()
        async with self.resource_prep.lease_for_run(
            __import__("uuid").UUID(request.account_id),
            __import__("uuid").UUID(request.run_id),
            control,
        ):
            yield

    @activity.defn(name="context_bootstrap_activity")
    async def context_bootstrap(self, request: ContextBootstrapInput) -> ContextBootstrapResult:
        self._check_schema(request.schema_version)
        started = time.monotonic()
        fields = self._correlation(request)
        log_event(context_logger, logging.INFO, "context_bootstrap_started", "context", **fields, status="started")
        if int(fields["activity_attempt"]) > 1:
            log_event(context_logger, logging.WARNING, "activity_retry_detected", "context", **fields, status="retrying")
        previous = await asyncio.to_thread(
            self.store.begin_operation, request.operation_id, request.run_id, "context"
        )
        if previous is not None:
            log_event(context_logger, logging.INFO, "operation_deduplicated", "context", **fields, status="deduplicated")
            return ContextBootstrapResult(**previous)
        try:
            loaded = await self.loader.load(request.run_id)
            if (
                loaded.account_id != request.account_id
                or loaded.conversation_id != request.conversation_id
                or loaded.session_id != request.session_id
            ):
                raise ApplicationError("context identity mismatch", non_retryable=True)
            messages = list(loaded.context)
            if loaded.context_provider is not None:
                recall_query, _ = await self.brain.rewrite_recall_query(
                    user_content=loaded.user_content,
                    group_context_text=loaded.group_context_text,
                    sender_name=loaded.sender_name,
                )
                memories = await loaded.context_provider.recall_long_term(recall_query)
                messages = list(loaded.context_provider.compose(memories))
            transcript_id = f"agent-transcript:{request.run_id}"
            version = await asyncio.to_thread(
                self.store.create_transcript,
                transcript_id=transcript_id,
                run_id=request.run_id,
                account_id=request.account_id,
                conversation_id=request.conversation_id,
                session_id=request.session_id,
                messages=messages,
                operation_id=request.operation_id,
            )
            result = ContextBootstrapResult(
                AGENT_SCHEMA_VERSION,
                transcript_id,
                version,
                f"transcript:{transcript_id}:{version}",
            )
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, StableExecutionFailure)
                else "context_build_failed"
            )
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, code)
            log_event(context_logger, logging.ERROR, "context_bootstrap_failed", "context", **fields, status="failed", error_code=code)
            if isinstance(exc, ApplicationError):
                raise
            raise ApplicationError(
                "上下文构建失败。",
                type=code,
                non_retryable=isinstance(exc, StableExecutionFailure),
            ) from exc
        log_event(context_logger, logging.INFO, "context_bootstrap_completed", "context", **fields, status="success", elapsed_ms=round((time.monotonic() - started) * 1000), transcript_version=version)
        return result

    @activity.defn(name="model_decision_activity")
    async def model_decision(self, request: ModelDecisionInput) -> ModelDecisionResult:
        self._check_schema(request.schema_version)
        started = time.monotonic()
        fields = self._correlation(request)
        log_event(model_logger, logging.INFO, "model_decision_started", "model", **fields, status="started")
        if int(fields["activity_attempt"]) > 1:
            log_event(model_logger, logging.WARNING, "activity_retry_detected", "model", **fields, status="retrying")
        previous = await asyncio.to_thread(
            self.store.begin_operation, request.operation_id, request.run_id,
            "synthesis" if request.final_only and request.plan_id else "model",
        )
        if previous is not None:
            log_event(model_logger, logging.INFO, "operation_deduplicated", "model", **fields, status="deduplicated", result_ref=previous.get("decision_ref"))
            return self._decision_from_payload(previous)
        events = self.event_factory.for_run(request.run_id)
        try:
            await asyncio.to_thread(
                self.store.validate_and_renew_lease,
                request.account_id,
                request.run_id,
                request.lease_token,
            )
            messages, actual_version = await asyncio.to_thread(
                self.store.load_messages, request.transcript_id
            )
            if actual_version != request.transcript_version:
                raise TranscriptVersionConflict("model input transcript version changed")
            model_messages = list(messages)
            if request.objective:
                objective_message = {
                    "role": "system",
                    "content": f"当前只执行此目标；完成后直接给出该步骤结果：{request.objective}",
                }
                position = 1 if model_messages and model_messages[0].get("role") == "system" else 0
                model_messages.insert(position, objective_message)
            if request.plan_id and request.step_id:
                await events.progress("executing_step", "正在执行计划步骤。")
            elif request.plan_id and request.final_only:
                await events.progress("synthesizing", "正在汇总计划结果。")
            else:
                await events.progress("calling_model", "正在生成回复。")
            async with self._workspace(request):
                self.actions.reset_turn(request.session_id, request.run_id)
                if request.final_only:
                    decision = await self.brain.generate_final_decision(messages=model_messages)
                else:
                    tools = await self.actions.select_tools(
                        user_content=request.objective or self._last_user_content(model_messages),
                        session_id=request.session_id,
                        execution_id=request.run_id,
                    )
                    decision = await self.brain.generate_chat_decision(
                        messages=model_messages,
                        tools=tools or None,
                    )
            result_ref = f"agent-decision:{request.operation_id}"
            calls = tuple(
                CompactToolCall(item.id, item.name, f"{result_ref}#{item.id}")
                for item in decision.action_requests
            )
            content = self._safe_final_content(decision.content) if not calls else decision.content
            message: dict[str, Any] = {"role": "assistant", "content": content}
            if calls:
                message["tool_calls"] = [
                    {
                        "id": item.id,
                        "name": item.name,
                        "arguments": dict(item.arguments),
                    }
                    for item in decision.action_requests
                ]
            payload = {
                "schema_version": AGENT_SCHEMA_VERSION,
                "operation_id": request.operation_id,
                "decision_type": "tool_calls" if calls else "final",
                "decision_ref": result_ref,
                "tool_calls": [asdict(item) for item in calls],
                "tool_call_arguments": {
                    item.id: dict(item.arguments)
                    for item in decision.action_requests
                },
                "transcript_version": request.transcript_version,
                "stop_reason": str(getattr(decision, "stop_reason", "")),
                "display_summary": content[:240],
                "content": content,
            }
            version = await asyncio.to_thread(
                self.store.complete_operation_with_event,
                transcript_id=request.transcript_id,
                expected_version=request.transcript_version,
                event_type="model_decision",
                operation_id=request.operation_id,
                event_payload={"message": message, "stop_reason": payload["stop_reason"]},
                result_ref=result_ref,
                result_payload=payload,
            )
            payload["transcript_version"] = version
            result = self._decision_from_payload(payload)
        except StaleFencingToken as exc:
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, exc.code)
            log_event(model_logger, logging.ERROR, "execution_fenced", "model", **fields, status="rejected", error_code=exc.code)
            raise ApplicationError("执行租约已失效。", type=exc.code, non_retryable=True) from exc
        except TranscriptVersionConflict as exc:
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, exc.code)
            raise ApplicationError("Agent transcript 版本冲突。", type=exc.code, non_retryable=True) from exc
        except Exception as exc:
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, "model_unavailable")
            log_event(model_logger, logging.ERROR, "model_decision_failed", "model", **fields, status="failed", error_code="model_unavailable", elapsed_ms=round((time.monotonic() - started) * 1000))
            if isinstance(exc, ApplicationError):
                raise
            raise ApplicationError("模型暂时不可用。", type="model_unavailable") from exc
        finally:
            self.actions.clear_execution(request.session_id, request.run_id)
            await events.close()
        log_event(model_logger, logging.INFO, "model_decision_completed", "model", **fields, status="success", elapsed_ms=round((time.monotonic() - started) * 1000), stop_reason=result.stop_reason, tool_count=len(result.tool_calls), result_ref=result.decision_ref)
        return result

    @activity.defn(name="tool_execution_activity")
    async def tool_execution(self, request: ToolExecutionInput) -> ToolExecutionResult:
        self._check_schema(request.schema_version)
        started = time.monotonic()
        fields = {
            **self._correlation(request),
            "tool_call_id": request.tool_call.tool_call_id,
            "tool": request.tool_call.name,
        }
        log_event(tool_logger, logging.INFO, "tool_execution_started", "tool", **fields, status="started")
        if int(fields["activity_attempt"]) > 1:
            log_event(tool_logger, logging.WARNING, "activity_retry_detected", "tool", **fields, status="retrying")
        previous = await asyncio.to_thread(
            self.store.begin_operation, request.operation_id, request.run_id, "tool"
        )
        if previous is not None:
            log_event(tool_logger, logging.INFO, "tool_execution_deduplicated", "tool", **fields, status="deduplicated", result_ref=previous.get("result_ref"))
            return ToolExecutionResult(**previous)
        events = self.event_factory.for_run(request.run_id)
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            await asyncio.to_thread(
                self.store.validate_and_renew_lease,
                request.account_id,
                request.run_id,
                request.lease_token,
            )
            _, actual_version = await asyncio.to_thread(
                self.store.load_messages, request.transcript_id
            )
            if actual_version != request.transcript_version:
                raise TranscriptVersionConflict("tool input transcript version changed")
            self._heartbeat(
                run_id=request.run_id,
                activity_kind="tool_execution",
                phase="executing",
                turn=request.turn,
                operation_id=request.operation_id,
                tool=request.tool_call.name,
                step_id=request.step_id,
            )
            heartbeat_task = asyncio.create_task(self._tool_heartbeat_loop(request))
            await events.progress("executing_tool", f"正在执行 {request.tool_call.name}。")
            async with self._workspace(request):
                side_effect_class = str(
                    self.actions.side_effect_class(request.session_id, request.tool_call.name)
                )
                if side_effect_class == "unknown":
                    raise ApplicationError(
                        "工具副作用分类未知。",
                        type="side_effect_class_unknown",
                        non_retryable=True,
                    )
                arguments = await asyncio.to_thread(
                    self.store.tool_call_arguments,
                    request.tool_call.arguments_ref,
                    request.tool_call.tool_call_id,
                )
                arguments_hash = hashlib.sha256(
                    json.dumps(
                        arguments,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()
                await asyncio.to_thread(
                    self.store.record_operation_intent,
                    request.operation_id,
                    {
                        "schema_version": 1,
                        "tool_call_id": request.tool_call.tool_call_id,
                        "tool": request.tool_call.name,
                        "arguments_hash": arguments_hash,
                        "side_effect_class": side_effect_class,
                        "fencing_token": request.lease_token,
                    },
                )
                action = ActionRequest(
                    request.tool_call.tool_call_id,
                    request.tool_call.name,
                    arguments,
                )
                result_value = await self.actions.execute_request(
                    action,
                    session_id=request.session_id,
                    execution_id=request.run_id,
                    user_query="",
                )
            display = result_value.display_result
            display_text = display if isinstance(display, str) else json.dumps(display, ensure_ascii=False, default=str)
            result_ref = f"agent-tool-result:{request.operation_id}"
            payload = {
                "schema_version": AGENT_SCHEMA_VERSION,
                "operation_id": request.operation_id,
                "result_ref": result_ref,
                "transcript_version": request.transcript_version,
                "display_summary": display_text[:240],
            }
            version = await asyncio.to_thread(
                self.store.complete_operation_with_event,
                transcript_id=request.transcript_id,
                expected_version=request.transcript_version,
                event_type="tool_result",
                operation_id=request.operation_id,
                event_payload={
                    "message": {
                        "role": "tool",
                        "tool_call_id": request.tool_call.tool_call_id,
                        "name": request.tool_call.name,
                        "content": display,
                    },
                    "raw_result": result_value.raw,
                    "side_effect_class": side_effect_class,
                },
                result_ref=result_ref,
                result_payload=payload,
            )
            payload["transcript_version"] = version
            result = ToolExecutionResult(**payload)
        except StaleFencingToken as exc:
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, exc.code)
            log_event(tool_logger, logging.ERROR, "tool_execution_fenced", "tool", **fields, status="rejected", error_code=exc.code, fencing_token=request.lease_token)
            raise ApplicationError("执行租约已失效。", type=exc.code, non_retryable=True) from exc
        except TranscriptVersionConflict as exc:
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, exc.code)
            raise ApplicationError("Agent transcript 版本冲突。", type=exc.code, non_retryable=True) from exc
        except Exception as exc:
            code = getattr(exc, "type", None) or "tool_failed"
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, str(code))
            log_event(tool_logger, logging.ERROR, "tool_execution_failed", "tool", **fields, status="failed", error_code=code, elapsed_ms=round((time.monotonic() - started) * 1000))
            if isinstance(exc, ApplicationError):
                raise
            raise ApplicationError("工具执行失败。", type="tool_failed", non_retryable=True) from exc
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            self.actions.clear_execution(request.session_id, request.run_id)
            await events.close()
        log_event(
            tool_logger,
            logging.INFO,
            "tool_execution_completed",
            "tool",
            **fields,
            status="success",
            elapsed_ms=round((time.monotonic() - started) * 1000),
            result_ref=result.result_ref,
            fencing_token=request.lease_token,
            side_effect_class=side_effect_class,
        )
        return result

    @activity.defn(name="planning_activity")
    async def planning(self, request: PlanningInput) -> PlanningResult:
        self._check_schema(request.schema_version)
        fields = self._correlation(request)
        log_event(plan_logger, logging.INFO, "planning_started", "planning", **fields, status="started")
        events = self.event_factory.for_run(request.run_id)
        await events.progress(
            "replanning" if request.plan_version > 1 else "planning",
            "正在调整执行计划。" if request.plan_version > 1 else "正在制定执行计划。",
        )
        previous = await asyncio.to_thread(
            self.store.begin_operation, request.operation_id, request.run_id, "planning"
        )
        if previous is not None:
            result = PlanningResult(
                schema_version=int(previous["schema_version"]),
                operation_id=str(previous["operation_id"]),
                plan_id=str(previous["plan_id"]),
                plan_version=int(previous["plan_version"]),
                steps=tuple(PlanStep(**item) for item in previous["steps"]),
                transcript_version=int(previous["transcript_version"]),
            )
            await events.progress("plan_ready", f"计划已恢复，共 {len(result.steps)} 步。")
            await events.close()
            return result
        try:
            await asyncio.to_thread(
                self.store.validate_and_renew_lease,
                request.account_id,
                request.run_id,
                request.lease_token,
            )
            messages, actual_version = await asyncio.to_thread(
                self.store.load_messages, request.transcript_id
            )
            if actual_version != request.transcript_version:
                raise TranscriptVersionConflict("planning transcript version changed")
            planning_messages = list(messages)
            planning_instruction = {
                "role": "system",
                "content": (
                    "把用户目标拆成 1 到 8 个可执行步骤。只返回 JSON："
                    '{"steps":[{"title":"短标题","objective":"具体目标"}]}。'
                ),
            }
            position = 1 if planning_messages and planning_messages[0].get("role") == "system" else 0
            planning_messages.insert(position, planning_instruction)
            decision = await self.brain.generate_final_decision(messages=planning_messages)
            raw_steps = self._parse_plan(
                decision.content, self._last_user_content(messages)
            )
            steps = tuple(
                PlanStep(f"step-{index}", index, title, objective)
                for index, (title, objective) in enumerate(raw_steps, 1)
            )
            result_ref = f"agent-plan:{request.plan_id}:v{request.plan_version}"
            payload = {
                "schema_version": AGENT_SCHEMA_VERSION,
                "operation_id": request.operation_id,
                "plan_id": request.plan_id,
                "plan_version": request.plan_version,
                "steps": [asdict(item) for item in steps],
                "transcript_version": request.transcript_version,
            }
            version = await asyncio.to_thread(
                self.store.complete_operation_with_event,
                transcript_id=request.transcript_id,
                expected_version=request.transcript_version,
                event_type="plan",
                operation_id=request.operation_id,
                event_payload={"plan_id": request.plan_id, "plan_version": request.plan_version, "steps": payload["steps"]},
                result_ref=result_ref,
                result_payload=payload,
            )
            payload["transcript_version"] = version
            result = PlanningResult(
                AGENT_SCHEMA_VERSION,
                request.operation_id,
                request.plan_id,
                request.plan_version,
                steps,
                version,
            )
        except Exception as exc:
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, "planning_failed")
            await events.close()
            if isinstance(exc, ApplicationError):
                raise
            raise ApplicationError("计划生成失败。", type="planning_failed") from exc
        log_event(plan_logger, logging.INFO, "planning_completed", "planning", **fields, status="success", plan_id=request.plan_id, plan_version=request.plan_version, step_count=len(result.steps))
        await events.progress("plan_ready", f"计划已生成，共 {len(result.steps)} 步。")
        await events.close()
        return result

    @activity.defn(name="evaluate_plan_activity")
    async def evaluate_plan(
        self, request: PlanEvaluationInput
    ) -> PlanEvaluationResult:
        self._check_schema(request.schema_version)
        fields = self._correlation(request)
        log_event(
            plan_logger,
            logging.INFO,
            "plan_evaluation_started",
            "planning",
            **fields,
            plan_id=request.plan_id,
            plan_version=request.plan_version,
            step_id=request.step_id,
            step_index=request.step_index,
            step_count=request.step_count,
            status="started",
        )
        previous = await asyncio.to_thread(
            self.store.begin_operation,
            request.operation_id,
            request.run_id,
            "planning",
        )
        if previous is not None:
            return PlanEvaluationResult(**previous)
        await asyncio.to_thread(
            self.store.validate_and_renew_lease,
            request.account_id,
            request.run_id,
            request.lease_token,
        )
        messages, actual_version = await asyncio.to_thread(
            self.store.load_messages, request.transcript_id
        )
        if actual_version != request.transcript_version:
            raise ApplicationError(
                "Agent transcript 版本冲突。",
                type="transcript_version_conflict",
                non_retryable=True,
            )
        default_decision = (
            "complete" if request.step_index >= request.step_count else "continue"
        )
        evaluation_messages = list(messages)
        instruction = {
            "role": "system",
            "content": (
                "评估刚完成的计划步骤。只返回 JSON："
                '{"decision":"continue|replan|complete|fail","reason":"简短原因"}。'
                f"当前是第 {request.step_index}/{request.step_count} 步。"
            ),
        }
        position = (
            1
            if evaluation_messages
            and evaluation_messages[0].get("role") == "system"
            else 0
        )
        evaluation_messages.insert(position, instruction)
        model_result = await self.brain.generate_final_decision(
            messages=evaluation_messages
        )
        decision, reason = self._parse_evaluation(
            model_result.content, default_decision
        )
        result = PlanEvaluationResult(
            AGENT_SCHEMA_VERSION,
            request.operation_id,
            cast(Any, decision),
            reason,
        )
        payload = asdict(result)
        await asyncio.to_thread(
            self.store.complete_operation,
            request.operation_id,
            f"agent-plan-evaluation:{request.operation_id}",
            payload,
        )
        events = self.event_factory.for_run(request.run_id)
        await events.progress("evaluating_step", "正在评估计划进度。")
        await events.close()
        log_event(
            plan_logger,
            logging.INFO,
            "plan_evaluation_completed",
            "planning",
            **fields,
            plan_id=request.plan_id,
            plan_version=request.plan_version,
            step_id=request.step_id,
            status="success",
            decision=decision,
        )
        return result

    @activity.defn(name="finalize_agent_result_activity")
    async def finalize_agent_result(self, request: FinalizeResultInput) -> dict[str, str]:
        self._check_schema(request.schema_version)
        content = await asyncio.to_thread(self.store.result_content, request.result_ref)
        authority = await asyncio.to_thread(
            self.lifecycle.complete, __import__("uuid").UUID(request.run_id), content
        )
        return {"run_id": authority.run_id, "status": authority.status}

    @staticmethod
    def _last_user_content(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                return str(message.get("content") or "")
        return ""

    @staticmethod
    def _safe_final_content(content: str | None) -> str:
        value = content or ""
        if "<tool_call>" in value:
            value = re.sub(r"<tool_call>.*?</tool_call>", "", value, flags=re.DOTALL).strip()
        return value or "抱歉，我暂时无法处理这个消息，请稍后重试。"

    @staticmethod
    def _parse_plan(
        content: str, fallback_objective: str = ""
    ) -> list[tuple[str, str]]:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            value = json.loads(text)
            items = value.get("steps", []) if isinstance(value, dict) else []
            parsed = []
            for item in items[:8]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "执行步骤").strip()[:160]
                objective = str(item.get("objective") or title).strip()[:4000]
                if objective:
                    parsed.append((title, objective))
            if parsed:
                return parsed
        except (TypeError, ValueError):
            pass
        fallback = fallback_objective.strip()[:4000] or "完成用户请求"
        return [("完成目标", fallback)]

    @staticmethod
    def _parse_evaluation(
        content: str, default_decision: str
    ) -> tuple[str, str]:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE
            )
        try:
            value = json.loads(text)
            decision = str(value.get("decision", ""))
            if decision in {"continue", "replan", "complete", "fail"}:
                return decision, str(value.get("reason") or "model_evaluation")[:500]
        except (AttributeError, TypeError, ValueError):
            pass
        return default_decision, "evaluation_contract_fallback"
