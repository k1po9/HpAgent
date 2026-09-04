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
from typing import Any, NoReturn, cast
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent.protocol import ActionRequest
from agent_execution.facade import StableExecutionFailure
from agent_execution.model_budget_context import model_budget_scope
from agent_execution.run_budget import RunBudgetExhausted
from agent_execution.tracing import (
    model_observation_metadata,
    trace_end,
    trace_node_id,
    trace_start,
)
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    ApprovalStatusInput,
    ApprovalStatusResult,
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

from .side_effects import (
    FaultInjector,
    NoopFaultInjector,
    ReconcileOutcome,
    ToolSideEffectReconciler,
    UnsupportedToolSideEffectReconciler,
    normalize_side_effect_class,
)
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
        run_budget: Any = None,
        reconciler: ToolSideEffectReconciler | None = None,
        fault_injector: FaultInjector | None = None,
        approval_service: Any = None,
        persistent_file_service: Any = None,
    ) -> None:
        self.store = store
        self.loader = loader
        self.brain = brain
        self.actions = actions
        self.event_factory = event_factory
        self.resource_prep = resource_prep
        self.lifecycle = lifecycle
        self.run_budget = run_budget
        self.reconciler = reconciler or UnsupportedToolSideEffectReconciler()
        self.fault_injector = fault_injector or NoopFaultInjector()
        self.approval_service = approval_service
        self.persistent_file_service = persistent_file_service

    @activity.defn(name="file_action_approval_status_activity")
    async def file_action_approval_status(
        self, request: ApprovalStatusInput,
    ) -> ApprovalStatusResult:
        self._check_schema(request.schema_version)
        if self.approval_service is None:
            raise ApplicationError(
                "approval service unavailable", non_retryable=True
            )
        approval = await asyncio.to_thread(
            self.approval_service.authoritative_status,
            UUID(request.account_id), UUID(request.run_id), request.operation_id,
            UUID(request.approval_id),
        )
        status = "approved" if approval.status == "consumed" else approval.status
        return ApprovalStatusResult(
            AGENT_SCHEMA_VERSION, str(approval.approval_id), approval.operation_id, status
        )

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

    async def _mark_uncertain_side_effect(
        self,
        request: ToolExecutionInput,
        fields: dict[str, Any],
        *,
        original_error_code: str,
        event: str = "tool_side_effect_uncertain",
        status: str = "failed",
        reason: str = "non_idempotent_side_effect_may_have_executed",
    ) -> None:
        """Persist an uncertain non-idempotent side effect before propagating."""
        await asyncio.to_thread(
            self.store.mark_operation_uncertain,
            request.operation_id,
            "tool_side_effect_uncertain",
        )
        log_event(
            tool_logger,
            logging.ERROR,
            event,
            "tool",
            **fields,
            status=status,
            error_code="tool_side_effect_uncertain",
            original_error_code=original_error_code,
            reason=reason,
        )

    async def _raise_uncertain_side_effect(
        self,
        request: ToolExecutionInput,
        fields: dict[str, Any],
        *,
        original_error_code: str,
        cause: BaseException | None = None,
    ) -> NoReturn:
        """Persist and expose the safety failure instead of its technical cause."""
        await self._mark_uncertain_side_effect(
            request,
            fields,
            original_error_code=original_error_code,
        )
        error = ApplicationError(
            "工具副作用状态无法安全确认。",
            type="tool_side_effect_uncertain",
            non_retryable=True,
        )
        if cause is not None:
            raise error from cause
        raise error

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
        events = self.event_factory.for_run(request.run_id)
        root_node_id = trace_node_id(request.run_id, "agent_execution")
        context_node_id = trace_node_id(
            request.run_id, "context_assembly", request.operation_id
        )
        memory_node_id = trace_node_id(
            request.run_id, "memory_recall", request.operation_id
        )
        rewrite_node_id = trace_node_id(
            request.run_id, "memory_query_rewrite", request.operation_id
        )
        await trace_start(
            events,
            root_node_id,
            None,
            "AgentExecution",
            "agent",
            {"strategy": request.strategy, "surface": "web"},
        )
        await trace_start(
            events,
            context_node_id,
            root_node_id,
            "ContextAssembly",
            "context",
            {"operation_id": request.operation_id},
        )
        log_event(context_logger, logging.INFO, "context_bootstrap_started", "context", **fields, status="started")
        if int(fields["activity_attempt"]) > 1:
            log_event(context_logger, logging.WARNING, "activity_retry_detected", "context", **fields, status="retrying")
        previous = await asyncio.to_thread(
            self.store.begin_operation, request.operation_id, request.run_id, "context"
        )
        if previous is not None:
            log_event(context_logger, logging.INFO, "operation_deduplicated", "context", **fields, status="deduplicated")
            await trace_end(
                events, context_node_id, "completed", {"deduplicated": True}
            )
            await events.close()
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
            memory_count = 0
            if loaded.context_provider is not None:
                await trace_start(
                    events,
                    rewrite_node_id,
                    context_node_id,
                    "MemoryQueryRewrite",
                    "llm",
                    {"model_selector": "fast"},
                )
                try:
                    with model_budget_scope(
                        self.run_budget, request.run_id,
                        f"{request.operation_id}:memory-rewrite",
                    ):
                        recall_query, rewrite_context = await self.brain.rewrite_recall_query(
                            user_content=loaded.user_content,
                            group_context_text=loaded.group_context_text,
                            sender_name=loaded.sender_name,
                        )
                except Exception as exc:
                    await trace_end(
                        events,
                        rewrite_node_id,
                        "failed",
                        {"error_code": type(exc).__name__},
                    )
                    raise
                await trace_end(
                    events,
                    rewrite_node_id,
                    "completed",
                    {"model_invoked": rewrite_context is not None},
                )
                await trace_start(
                    events,
                    memory_node_id,
                    context_node_id,
                    "MemoryRecall",
                    "memory",
                )
                try:
                    memories = await loaded.context_provider.recall_long_term(recall_query)
                    memory_count = len(memories)
                    messages = list(loaded.context_provider.compose(memories))
                except Exception as exc:
                    await trace_end(
                        events,
                        memory_node_id,
                        "failed",
                        {"error_code": getattr(exc, "code", type(exc).__name__)},
                    )
                    raise
                await trace_end(
                    events,
                    memory_node_id,
                    "completed",
                    {"memory_count": memory_count},
                )
            else:
                await trace_start(
                    events,
                    memory_node_id,
                    context_node_id,
                    "MemoryRecall",
                    "memory",
                )
                await trace_end(
                    events, memory_node_id, "completed", {"memory_count": 0}
                )
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
            await trace_end(
                events,
                context_node_id,
                "completed",
                {"message_count": len(messages), "memory_count": memory_count},
            )
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, StableExecutionFailure)
                else "context_build_failed"
            )
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, code)
            await trace_end(
                events, context_node_id, "failed", {"error_code": code}
            )
            log_event(context_logger, logging.ERROR, "context_bootstrap_failed", "context", **fields, status="failed", error_code=code)
            if isinstance(exc, ApplicationError):
                raise
            raise ApplicationError(
                "上下文构建失败。",
                type=code,
                non_retryable=isinstance(exc, StableExecutionFailure),
            ) from exc
        finally:
            await events.close()
        log_event(context_logger, logging.INFO, "context_bootstrap_completed", "context", **fields, status="success", elapsed_ms=round((time.monotonic() - started) * 1000), transcript_version=version)
        return result

    @activity.defn(name="model_decision_activity")
    async def model_decision(self, request: ModelDecisionInput) -> ModelDecisionResult:
        self._check_schema(request.schema_version)
        started = time.monotonic()
        fields = self._correlation(request)
        events = self.event_factory.for_run(request.run_id)
        root_node_id = trace_node_id(request.run_id, "agent_execution")
        model_node_id = trace_node_id(request.run_id, "llm_call", request.operation_id)
        model_phase = (
            "synthesis"
            if request.final_only and request.plan_id
            else "forced_final"
            if request.final_only
            else "plan_step"
            if request.plan_id and request.step_id
            else "decision"
        )
        await trace_start(
            events,
            model_node_id,
            root_node_id,
            "LLMCall",
            "llm",
            {
                "operation_id": request.operation_id,
                "turn": request.turn,
                "phase": model_phase,
                "model_selector": "chat",
            },
        )
        log_event(model_logger, logging.INFO, "model_decision_started", "model", **fields, status="started")
        if int(fields["activity_attempt"]) > 1:
            log_event(model_logger, logging.WARNING, "activity_retry_detected", "model", **fields, status="retrying")
        previous = await asyncio.to_thread(
            self.store.begin_operation, request.operation_id, request.run_id,
            "synthesis" if request.final_only and request.plan_id else "model",
        )
        if previous is not None:
            log_event(model_logger, logging.INFO, "operation_deduplicated", "model", **fields, status="deduplicated", result_ref=previous.get("decision_ref"))
            result = self._decision_from_payload(previous)
            await trace_end(
                events,
                model_node_id,
                "completed",
                {"deduplicated": True, "stop_reason": result.stop_reason},
            )
            await events.close()
            return result
        trace_status = "failed"
        trace_metadata: dict[str, Any] = {"error_code": "model_unavailable"}
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
                with model_budget_scope(
                    self.run_budget, request.run_id, request.operation_id,
                    final_response=request.final_only,
                ):
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
            input_context = getattr(decision, "input_context", None) or {}
            trace_status = "completed"
            trace_metadata = {
                "stop_reason": result.stop_reason,
                "tool_count": len(result.tool_calls),
                "estimated_input_tokens": input_context.get("estimated_tokens"),
                **model_observation_metadata(decision),
            }
        except StaleFencingToken as exc:
            trace_metadata = {"error_code": exc.code}
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, exc.code)
            log_event(model_logger, logging.ERROR, "execution_fenced", "model", **fields, status="rejected", error_code=exc.code)
            raise ApplicationError("执行租约已失效。", type=exc.code, non_retryable=True) from exc
        except TranscriptVersionConflict as exc:
            trace_metadata = {"error_code": exc.code}
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, exc.code)
            raise ApplicationError("Agent transcript 版本冲突。", type=exc.code, non_retryable=True) from exc
        except Exception as exc:
            if isinstance(exc, RunBudgetExhausted):
                trace_metadata = {"error_code": exc.code}
                await asyncio.to_thread(
                    self.store.fail_operation, request.operation_id, exc.code
                )
                raise ApplicationError(
                    "Run 执行预算已耗尽。", type=exc.code, non_retryable=True
                ) from exc
            trace_metadata = {
                "error_code": getattr(exc, "type", None) or "model_unavailable"
            }
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, "model_unavailable")
            log_event(model_logger, logging.ERROR, "model_decision_failed", "model", **fields, status="failed", error_code="model_unavailable", elapsed_ms=round((time.monotonic() - started) * 1000))
            if isinstance(exc, ApplicationError):
                raise
            raise ApplicationError("模型暂时不可用。", type="model_unavailable") from exc
        finally:
            self.actions.clear_execution(request.session_id, request.run_id)
            await trace_end(events, model_node_id, trace_status, trace_metadata)
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
        events = self.event_factory.for_run(request.run_id)
        root_node_id = trace_node_id(request.run_id, "agent_execution")
        tool_node_id = trace_node_id(request.run_id, "tool_execution", request.operation_id)
        await trace_start(
            events,
            tool_node_id,
            root_node_id,
            "ToolExecution",
            "tool",
            {
                "operation_id": request.operation_id,
                "tool_name": request.tool_call.name,
                "tool_call_id": request.tool_call.tool_call_id,
                "turn": request.turn,
            },
        )
        log_event(tool_logger, logging.INFO, "tool_execution_started", "tool", **fields, status="started")
        if int(fields["activity_attempt"]) > 1:
            log_event(tool_logger, logging.WARNING, "activity_retry_detected", "tool", **fields, status="retrying")
        operation = await asyncio.to_thread(
            self.store.begin_tool_operation, request.operation_id, request.run_id
        )
        if operation.status == "completed":
            previous = operation.result_payload or {}
            log_event(tool_logger, logging.INFO, "tool_execution_deduplicated", "tool", **fields, status="deduplicated", result_ref=previous.get("result_ref"))
            result = ToolExecutionResult(**previous)
            await trace_end(
                events, tool_node_id, "completed", {"deduplicated": True}
            )
            await events.close()
            return result
        heartbeat_task: asyncio.Task[None] | None = None
        file_node_name = {
            "inspect_file": "FileInspect",
            "search_file": "FileSearch",
            "count_matches": "FileCount",
            "text_stats": "FileInspect",
            "create_docx": "FileTransform",
            "create_workbook": "FileTransform",
            "create_presentation": "FileTransform",
            "convert_file_to_pdf": "FileTransform",
            "replace_docx_text": "FileTransform",
            "append_docx_section": "FileTransform",
            "write_sheet_range": "FileTransform",
            "replace_slide": "FileTransform",
        }.get(request.tool_call.name)
        file_node_id = (
            trace_node_id(request.run_id, "file_tool", request.operation_id)
            if file_node_name
            else None
        )
        file_trace_metadata: dict[str, Any] = (
            {"query_mode": "literal"}
            if file_node_name == "FileSearch"
            else {}
        )
        if file_node_id is not None and file_node_name is not None:
            await trace_start(
                events,
                file_node_id,
                tool_node_id,
                file_node_name,
                "file",
                dict(file_trace_metadata),
            )
        side_effect_class = "unknown"
        persisted_side_effect_class = normalize_side_effect_class(
            str((operation.result_payload or {}).get("side_effect_class", "unknown"))
        )
        non_idempotent_may_have_executed = (
            operation.status in {"intent_recorded", "uncertain"}
            and persisted_side_effect_class == "non_idempotent_write"
        )
        trace_status = "failed"
        trace_metadata: dict[str, Any] = {"error_code": "tool_failed"}
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
            await events.progress("executing_tool", f"正在执行 {request.tool_call.name}。")
            async with self._workspace(request):
                # The first check happened before potentially waiting on the
                # in-process account lock. Renew again after workspace recovery
                # and immediately before entering the side-effect window.
                await asyncio.to_thread(
                    self.store.validate_and_renew_lease,
                    request.account_id,
                    request.run_id,
                    request.lease_token,
                )
                heartbeat_task = asyncio.create_task(
                    self._tool_heartbeat_loop(request)
                )
                if (request.tool_call.name == "save_persistent_file"
                        and self.persistent_file_service is not None):
                    arguments = await asyncio.to_thread(
                        self.store.tool_call_arguments,
                        request.tool_call.arguments_ref,
                        request.tool_call.tool_call_id,
                    )
                    persistent = await asyncio.to_thread(
                        self.persistent_file_service.save,
                        UUID(request.account_id), UUID(request.run_id), request.operation_id,
                        str(arguments["logical_path"]), UUID(str(arguments["source_file_id"])),
                    )
                    if persistent.status == "approval_required":
                        approval = await asyncio.to_thread(
                            self.approval_service.authoritative_status,
                            UUID(request.account_id), UUID(request.run_id),
                            request.operation_id, persistent.approval_id,
                        )
                        trace_status = "completed"
                        return ToolExecutionResult(
                            AGENT_SCHEMA_VERSION, request.operation_id,
                            f"file-approval:{persistent.approval_id}",
                            request.transcript_version, "approval required",
                            str(persistent.approval_id), "pending",
                            approval.expires_at.isoformat(),
                        )
                side_effect_class = normalize_side_effect_class(str(
                    self.actions.side_effect_class(request.session_id, request.tool_call.name)
                ))
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
                intent = operation.result_payload or {
                        "schema_version": 1,
                        "tool_call_id": request.tool_call.tool_call_id,
                        "tool": request.tool_call.name,
                        "arguments_hash": arguments_hash,
                        "side_effect_class": side_effect_class,
                        "fencing_token": request.lease_token,
                        "idempotency_key": request.operation_id,
                    }
                if operation.status in {"intent_recorded", "uncertain"}:
                    if intent.get("arguments_hash") != arguments_hash or intent.get("tool") != request.tool_call.name:
                        raise ApplicationError("工具恢复标识不一致。", type="side_effect_reconciliation_failed", non_retryable=True)
                    recovered_side_effect_class = normalize_side_effect_class(
                        str(intent.get("side_effect_class", "unknown"))
                    )
                    if recovered_side_effect_class != side_effect_class:
                        raise ApplicationError(
                            "工具副作用分类与持久化 intent 不一致。",
                            type="side_effect_reconciliation_failed",
                            non_retryable=True,
                        )
                    side_effect_class = recovered_side_effect_class
                    if side_effect_class == "non_idempotent_write":
                        log_event(tool_logger, logging.WARNING, "tool_side_effect_reconcile_started", "tool", **fields, status="started")
                        reconciled = await self.reconciler.reconcile(
                            operation_id=request.operation_id,
                            tool_name=request.tool_call.name,
                            arguments_hash=arguments_hash,
                            intent=intent,
                        )
                        log_event(tool_logger, logging.WARNING, "tool_side_effect_reconcile_completed", "tool", **fields, status=reconciled.outcome.value)
                        if reconciled.outcome == ReconcileOutcome.CONFIRMED_COMPLETED:
                            result_value = reconciled.result
                            if result_value is None:
                                raise ApplicationError("副作用已完成但结果不可恢复。", type="side_effect_reconciliation_failed", non_retryable=True)
                        elif reconciled.outcome != ReconcileOutcome.CONFIRMED_NOT_EXECUTED:
                            await self._raise_uncertain_side_effect(
                                request,
                                fields,
                                original_error_code="side_effect_reconciliation_unsupported",
                            )
                        else:
                            result_value = None
                    else:
                        result_value = None
                else:
                    await asyncio.to_thread(
                        self.store.record_operation_intent,
                        request.operation_id,
                        intent,
                    )
                    log_event(tool_logger, logging.INFO, "tool_side_effect_intent_recorded", "tool", **fields, status="success", side_effect_class=side_effect_class)
                    result_value = None
                action = ActionRequest(
                    request.tool_call.tool_call_id,
                    request.tool_call.name,
                    arguments,
                )
                if result_value is None:
                    # Reconciliation and intent persistence may themselves
                    # take time. Fence once more at the last possible point.
                    await asyncio.to_thread(
                        self.store.validate_and_renew_lease,
                        request.account_id,
                        request.run_id,
                        request.lease_token,
                    )
                    if side_effect_class == "non_idempotent_write":
                        non_idempotent_may_have_executed = True
                    reservation = {"tool_calls": 1}
                    if self.run_budget is not None:
                        reservation = self.actions.budget_reservation(
                            request.session_id, request.tool_call.name
                        )
                        try:
                            await asyncio.to_thread(
                                self.run_budget.reserve,
                                request.run_id,
                                request.operation_id,
                                reservation,
                            )
                        except RunBudgetExhausted as exc:
                            raise ApplicationError(
                                "Run 执行预算已耗尽。",
                                type=exc.code,
                                non_retryable=True,
                            ) from exc
                    try:
                        with model_budget_scope(
                            self.run_budget, request.run_id,
                            f"{request.operation_id}:tool-summary",
                        ):
                            result_value = await self.actions.execute_request(
                                action,
                                session_id=request.session_id,
                                execution_id=request.run_id,
                                user_query="",
                                idempotency_key=request.operation_id,
                            )
                    except BaseException:
                        if self.run_budget is not None:
                            await asyncio.to_thread(
                                self.run_budget.settle,
                                request.run_id,
                                request.operation_id,
                                reservation,
                                "estimated",
                            )
                        raise
                    if self.run_budget is not None:
                        measured = {dimension: 0 for dimension in reservation}
                        measured["tool_calls"] = 1
                        raw_usage = (result_value.metadata or {}).get("budget_usage")
                        if isinstance(raw_usage, dict):
                            for dimension in measured:
                                amount = raw_usage.get(dimension)
                                if isinstance(amount, int) and not isinstance(amount, bool):
                                    measured[dimension] = max(0, amount)
                        await asyncio.to_thread(
                            self.run_budget.settle,
                            request.run_id,
                            request.operation_id,
                            measured,
                            "measured",
                        )
                    budget_usage = (result_value.metadata or {}).get("budget_usage")
                    if isinstance(budget_usage, dict):
                        file_trace_metadata.update({
                            "scanned_bytes": budget_usage.get("bytes_scanned", 0),
                            "returned_bytes": budget_usage.get(
                                "bytes_returned_to_model", 0
                            ),
                        })
                    result_trace = (result_value.metadata or {}).get("trace_metadata")
                    if isinstance(result_trace, dict):
                        file_trace_metadata.update(result_trace)
                    if (
                        side_effect_class == "non_idempotent_write"
                        and result_value.error is not None
                    ):
                        await self._raise_uncertain_side_effect(
                            request,
                            fields,
                            original_error_code="action_result_error",
                        )
                    self.fault_injector.hit("tool_side_effect_succeeded_before_ack")
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
            trace_status = "completed"
            trace_metadata = {
                "side_effect_class": side_effect_class,
                "result_ref": result.result_ref,
            }
        except asyncio.CancelledError:
            trace_status = "cancelled"
            trace_metadata = {"error_code": "activity_cancelled"}
            if non_idempotent_may_have_executed:
                await self._mark_uncertain_side_effect(
                    request,
                    fields,
                    original_error_code="activity_cancelled",
                    event="tool_execution_cancelled",
                    status="cancelled",
                    reason="cancelled_after_non_idempotent_side_effect_window",
                )
            else:
                await asyncio.to_thread(
                    self.store.fail_operation,
                    request.operation_id,
                    "activity_cancelled",
                )
                log_event(
                    tool_logger,
                    logging.WARNING,
                    "tool_execution_cancelled",
                    "tool",
                    **fields,
                    status="cancelled",
                    error_code="activity_cancelled",
                    reason="cancelled_before_non_idempotent_side_effect_window",
                    side_effect_class=side_effect_class,
                )
            raise
        except StaleFencingToken as exc:
            trace_metadata = {"error_code": exc.code}
            if non_idempotent_may_have_executed:
                await self._raise_uncertain_side_effect(
                    request,
                    fields,
                    original_error_code=exc.code,
                    cause=exc,
                )
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, exc.code)
            log_event(tool_logger, logging.ERROR, "tool_execution_fenced", "tool", **fields, status="rejected", error_code=exc.code, fencing_token=request.lease_token)
            raise ApplicationError("执行租约已失效。", type=exc.code, non_retryable=True) from exc
        except TranscriptVersionConflict as exc:
            trace_metadata = {"error_code": exc.code}
            if non_idempotent_may_have_executed:
                await self._raise_uncertain_side_effect(
                    request,
                    fields,
                    original_error_code=exc.code,
                    cause=exc,
                )
            await asyncio.to_thread(self.store.fail_operation, request.operation_id, exc.code)
            raise ApplicationError("Agent transcript 版本冲突。", type=exc.code, non_retryable=True) from exc
        except Exception as exc:
            code = getattr(exc, "type", None) or "tool_failed"
            trace_metadata = {"error_code": str(code)}
            if str(code) == "tool_side_effect_uncertain":
                pass
            elif non_idempotent_may_have_executed:
                await self._raise_uncertain_side_effect(
                    request,
                    fields,
                    original_error_code=str(code),
                    cause=exc,
                )
            else:
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
            if file_node_id is not None:
                await trace_end(
                    events, file_node_id, trace_status, file_trace_metadata
                )
            await trace_end(events, tool_node_id, trace_status, trace_metadata)
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
        root_node_id = trace_node_id(request.run_id, "agent_execution")
        model_node_id = trace_node_id(request.run_id, "llm_call", request.operation_id)
        await trace_start(
            events,
            model_node_id,
            root_node_id,
            "LLMCall",
            "llm",
            {
                "operation_id": request.operation_id,
                "phase": "replanning" if request.plan_version > 1 else "planning",
                "model_selector": "chat",
                "plan_id": request.plan_id,
                "plan_version": request.plan_version,
            },
        )
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
            await trace_end(
                events,
                model_node_id,
                "completed",
                {"deduplicated": True, "step_count": len(result.steps)},
            )
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
                    "不要重复已经完成且仍有效的步骤，必须利用 completed step results。"
                    f" previous_plan_ref={request.previous_plan_ref!r};"
                    f" previous_plan_version={request.previous_plan_version!r};"
                    f" completed_step_refs={list(request.completed_step_refs)!r};"
                    f" trigger_step_id={request.trigger_step_id!r};"
                    f" evaluation_reason={request.evaluation_reason!r}."
                ),
            }
            position = 1 if planning_messages and planning_messages[0].get("role") == "system" else 0
            planning_messages.insert(position, planning_instruction)
            with model_budget_scope(
                self.run_budget, request.run_id, request.operation_id
            ):
                decision = await self.brain.generate_final_decision(
                    messages=planning_messages
                )
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
                event_payload={
                    "plan_id": request.plan_id,
                    "plan_version": request.plan_version,
                    "steps": payload["steps"],
                    "previous_plan_ref": request.previous_plan_ref,
                    "previous_plan_version": request.previous_plan_version,
                    "completed_step_refs": list(request.completed_step_refs),
                    "trigger_step_id": request.trigger_step_id,
                    "evaluation_reason": request.evaluation_reason,
                },
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
            failure_code = (
                exc.code if isinstance(exc, RunBudgetExhausted) else "planning_failed"
            )
            await asyncio.to_thread(
                self.store.fail_operation, request.operation_id, failure_code
            )
            await trace_end(
                events,
                model_node_id,
                "failed",
                {"error_code": getattr(exc, "type", None) or failure_code},
            )
            await events.close()
            if isinstance(exc, ApplicationError):
                raise
            if isinstance(exc, RunBudgetExhausted):
                raise ApplicationError(
                    "Run 执行预算已耗尽。", type=exc.code, non_retryable=True
                ) from exc
            raise ApplicationError("计划生成失败。", type="planning_failed") from exc
        log_event(
            plan_logger,
            logging.INFO,
            "planning_completed",
            "planning",
            **fields,
            status="success",
            step_count=len(result.steps),
        )
        await events.progress("plan_ready", f"计划已生成，共 {len(result.steps)} 步。")
        await trace_end(
            events,
            model_node_id,
            "completed",
            {
                "step_count": len(result.steps),
                "stop_reason": getattr(decision, "stop_reason", ""),
                **model_observation_metadata(decision),
            },
        )
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
        events = self.event_factory.for_run(request.run_id)
        await events.progress("evaluating_step", "正在评估计划进度。")
        root_node_id = trace_node_id(request.run_id, "agent_execution")
        model_node_id = trace_node_id(request.run_id, "llm_call", request.operation_id)
        await trace_start(
            events,
            model_node_id,
            root_node_id,
            "LLMCall",
            "llm",
            {
                "operation_id": request.operation_id,
                "phase": "plan_evaluation",
                "model_selector": "chat",
                "plan_id": request.plan_id,
                "plan_version": request.plan_version,
                "step_id": request.step_id,
            },
        )
        try:
            with model_budget_scope(
                self.run_budget, request.run_id, request.operation_id
            ):
                model_result = await self.brain.generate_final_decision(
                    messages=evaluation_messages
                )
        except Exception as exc:
            await trace_end(
                events,
                model_node_id,
                "failed",
                {"error_code": getattr(exc, "type", None) or "model_unavailable"},
            )
            await events.close()
            if isinstance(exc, RunBudgetExhausted):
                raise ApplicationError(
                    "Run 执行预算已耗尽。", type=exc.code, non_retryable=True
                ) from exc
            raise
        await trace_end(
            events,
            model_node_id,
            "completed",
            {
                "stop_reason": getattr(model_result, "stop_reason", ""),
                **model_observation_metadata(model_result),
            },
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
        await events.close()
        log_event(
            plan_logger,
            logging.INFO,
            "plan_evaluation_completed",
            "planning",
            **fields,
            status="success",
            decision=decision,
        )
        return result

    @activity.defn(name="finalize_agent_result_activity")
    async def finalize_agent_result(self, request: FinalizeResultInput) -> dict[str, str]:
        self._check_schema(request.schema_version)
        events = self.event_factory.for_run(request.run_id)
        root_node_id = trace_node_id(request.run_id, "agent_execution")
        final_node_id = trace_node_id(request.run_id, "final_response")
        await trace_start(
            events,
            final_node_id,
            root_node_id,
            "FinalResponse",
            "final",
            {"result_ref": request.result_ref},
        )
        try:
            content = await asyncio.to_thread(self.store.result_content, request.result_ref)
            authority = await asyncio.to_thread(
                self.lifecycle.complete, __import__("uuid").UUID(request.run_id), content
            )
            await trace_end(
                events,
                final_node_id,
                "completed",
                {"content_length": len(content)},
            )
            await trace_end(events, root_node_id, "completed")
            return {"run_id": authority.run_id, "status": authority.status}
        except Exception as exc:
            await trace_end(
                events,
                final_node_id,
                "failed",
                {"error_code": getattr(exc, "type", None) or type(exc).__name__},
            )
            raise
        finally:
            await events.close()

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
