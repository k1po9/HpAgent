"""QQ compatibility Host for the channel-neutral execution Facade (D-05)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID, uuid5

from .facade import (
    AgentExecutionFacade,
    EventSink,
    ExecutionAuditSinkFactory,
    ExecutionControl,
    ExecutionRequest,
    ExecutionResult,
)

HPAGENT_QQ_TURN_NS = UUID("e5c56536-48a5-5a84-bf54-1dbfa11c4fb4")


def qq_execution_id(workflow_id: str, message_id: str) -> str:
    if not workflow_id or not message_id:
        raise ValueError("QQ workflow_id and normalized message_id are required")
    return f"qq-turn-{uuid5(HPAGENT_QQ_TURN_NS, f'{workflow_id}:{message_id}')}"


def legacy_qq_message_id(workflow_id: str, user_message: dict[str, Any]) -> str:
    """Deterministic compatibility ID for pre-message_id Workflow histories."""
    required = ("session_id", "sender_id", "timestamp", "content")
    if not workflow_id or any(key not in user_message for key in required):
        raise ValueError("legacy QQ message lacks deterministic identity fields")
    content_hash = hashlib.sha256(str(user_message["content"]).encode("utf-8")).hexdigest()
    stable = ":".join(
        (
            workflow_id,
            str(user_message["session_id"]),
            str(user_message["sender_id"]),
            str(user_message["timestamp"]),
            content_hash,
        )
    )
    return str(uuid5(HPAGENT_QQ_TURN_NS, stable))


class QQRequestLoader(Protocol):
    async def load(
        self, user_message: dict[str, Any], execution_id: str
    ) -> ExecutionRequest: ...


class QQLegacyContextProvider:
    """Two-phase adapter over the existing QQ Session/Context services."""

    def __init__(
        self,
        memory_service: Any,
        context_builder: Any,
        events: list[Any],
        user_message: dict[str, Any],
        group_context_text: str,
    ) -> None:
        self._memory = memory_service
        self._context = context_builder
        self._events = events
        self._message = user_message
        self._group_context_text = group_context_text

    async def recall_long_term(self, recall_query: str) -> Sequence[Any]:
        metadata = dict(self._message.get("metadata", {}))
        metadata.pop("channel_overrides", None)
        metadata.pop("execution_id", None)
        _items, memories_text = await self._memory.recall_memories(
            query=recall_query,
            account_id=str(self._message["account_id"]),
            session_id=str(self._message["session_id"]),
            channel_type=str(self._message["channel_type"]),
            metadata=metadata,
            original_query=str(self._message["content"]),
            rewritten_query=recall_query,
            hyde_input_context=None,
        )
        return (memories_text,)

    def compose(self, memories: Sequence[Any]) -> tuple[dict[str, Any], ...]:
        from common.types import ChannelType

        raw_channel = str(self._message["channel_type"])
        try:
            channel = ChannelType(raw_channel)
        except ValueError:
            channel = None
        recalled = str(memories[0]) if memories else ""
        return tuple(self._context.build(
            events=self._events,
            channel_type=channel,
            recalled_memories=recalled,
            max_turns=20,
            group_context_text=self._group_context_text,
        ))


class QQLegacyRequestLoader:
    """Resolve legacy QQ DTOs before entering the channel-neutral Facade."""

    _METADATA_ALLOWLIST = frozenset({
        "detail_type",
        "group_id",
        "sender_name",
        "iso_timestamp",
        "self_id",
    })

    def __init__(
        self,
        memory_service: Any,
        context_builder: Any,
        group_context: Any = None,
        channel_overrides: Mapping[str, Any] | None = None,
    ) -> None:
        self._memory = memory_service
        self._context = context_builder
        self._group_context = group_context
        self._channel_overrides = channel_overrides or {}

    async def load(
        self, user_message: dict[str, Any], execution_id: str
    ) -> ExecutionRequest:
        account_id = str(user_message["account_id"])
        session_id = str(user_message["session_id"])
        channel_type = str(user_message["channel_type"])
        metadata = {
            key: value
            for key, value in dict(user_message.get("metadata", {})).items()
            if key in self._METADATA_ALLOWLIST
        }
        metadata["execution_id"] = execution_id
        metadata["channel_overrides"] = dict(
            self._channel_overrides.get(channel_type, {})
        )
        await self._memory.ensure_session(session_id, account_id, channel_type)

        group_context_text = ""
        group_id = str(metadata.get("group_id", ""))
        if group_id and self._group_context is not None:
            try:
                await self._group_context.subscribe(group_id, session_id)
                group_context_text = await self._group_context.get_window(group_id)
            except Exception:
                group_context_text = ""

        events = await self._memory.load_recent_events(session_id, limit=100)
        if not any(
            getattr(event, "metadata", {}).get("execution_id") == execution_id
            for event in events
        ):
            user_event = await self._memory.record_user_message(
                session_id=session_id,
                content=str(user_message["content"]),
                sender_id=str(user_message["sender_id"]),
                channel_type=channel_type,
                account_id=account_id,
                metadata=metadata,
            )
            events.append(user_event)

        safe_message = dict(user_message)
        safe_message["metadata"] = metadata
        provider = QQLegacyContextProvider(
            self._memory,
            self._context,
            events,
            safe_message,
            group_context_text,
        )
        profile = (
            "qq_group"
            if metadata.get("detail_type") == "group"
            else "qq_private"
        )
        return ExecutionRequest(
            execution_id=execution_id,
            account_id=account_id,
            conversation_id="",
            session_id=session_id,
            user_content=str(user_message["content"]),
            context=provider.compose(()),
            trigger_message_id=str(user_message["message_id"]),
            interaction_profile=profile,
            metadata=metadata,
            context_provider=provider,
        )


class QQEventSinkFactory(Protocol):
    def for_execution(self, execution_id: str, user_message: dict[str, Any]) -> EventSink: ...


class QQReplySink(Protocol):
    async def complete(
        self, user_message: dict[str, Any], result: ExecutionResult
    ) -> None: ...


class QQRetentionSink(Protocol):
    async def retain(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
        user_message: dict[str, Any],
    ) -> None: ...


class QQExecutionHost:
    """Maps the frozen legacy DTO to Facade ports and preserves its result DTO."""

    def __init__(
        self,
        loader: QQRequestLoader,
        facade: AgentExecutionFacade,
        events: QQEventSinkFactory,
        replies: QQReplySink,
        control: ExecutionControl,
        *,
        allow_legacy_missing_message_id: bool = False,
        audit: ExecutionAuditSinkFactory | None = None,
        retention: QQRetentionSink | None = None,
    ) -> None:
        self._loader = loader
        self._facade = facade
        self._events = events
        self._replies = replies
        self._control = control
        self._allow_legacy = allow_legacy_missing_message_id
        self._audit = audit
        self._retention = retention

    async def execute(
        self, workflow_id: str, user_message: dict[str, Any]
    ) -> dict[str, Any]:
        message_id = user_message.get("message_id")
        if not message_id:
            if not self._allow_legacy:
                raise ValueError("new QQ ingress payload must contain message_id")
            message_id = legacy_qq_message_id(workflow_id, user_message)
        execution_id = qq_execution_id(workflow_id, str(message_id))
        request = await self._loader.load(user_message, execution_id)
        if request.execution_id != execution_id:
            raise ValueError("QQ request loader returned a different execution")
        events = self._events.for_execution(execution_id, user_message)
        try:
            audit = (
                self._audit.for_execution(request.execution_id, request)
                if self._audit is not None
                else None
            )
            result = await self._facade.execute(
                request, self._control, events, audit
            )
            await self._replies.complete(user_message, result)
            if self._retention is not None:
                await self._retention.retain(request, result, user_message)
            return {
                "content": result.content,
                "turns": result.tool_turns,
                "session_id": request.session_id,
                "account_id": request.account_id,
            }
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                await close()


class QQLegacyExecutionControl:
    """QQ keeps its existing Temporal Activity timeout/cancellation behavior."""

    @property
    def deadline(self) -> datetime:
        return datetime.max.replace(tzinfo=UTC)

    def cancelled(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        return None

    async def heartbeat(self, phase: str) -> None:
        return None


class ReplyServiceQQSink:
    """Keep final QQ delivery on the existing ReplyService/ChannelRouter path."""

    def __init__(self, reply_service: Any) -> None:
        self._reply = reply_service

    async def complete(
        self, user_message: dict[str, Any], result: ExecutionResult
    ) -> None:
        await self._reply.send_final(result.content, user_message)


class TurnMemoryQQRetentionSink:
    """Keep QQ retain after final delivery, matching the legacy ordering.

    Phase F (F-03): 只 retain 用户实际说的话 + 最终实际收到的答案，使用稳定
    的 per-execution document_id（doc §28-31），不再用本轮内容反复覆盖同一个
    session document。
    """

    def __init__(self, memory_service: Any) -> None:
        self._memory = memory_service

    async def retain(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
        user_message: dict[str, Any],
    ) -> None:
        events = [
            {"role": "user", "content": request.user_content},
            {"role": "assistant", "content": result.content},
        ]
        document_id = f"qq-execution:{request.execution_id}"
        metadata = dict(user_message.get("metadata", {}))
        metadata["source"] = "qq"
        metadata["execution_id"] = request.execution_id
        metadata["session_id"] = request.session_id
        await self._memory.retain_document(
            events=events,
            account_id=request.account_id,
            document_id=document_id,
            channel_type=str(user_message["channel_type"]),
            metadata=metadata,
            session_id=request.session_id,
        )


class TurnMemoryQQAuditSinkFactory:
    def __init__(self, memory_service: Any) -> None:
        self._memory = memory_service

    def for_execution(
        self, execution_id: str, request: ExecutionRequest
    ) -> "TurnMemoryQQAuditSink":
        if execution_id != request.execution_id:
            raise ValueError("QQ audit execution identity mismatch")
        return TurnMemoryQQAuditSink(self._memory, request)


class TurnMemoryQQAuditSink:
    def __init__(self, memory_service: Any, request: ExecutionRequest) -> None:
        self._memory = memory_service
        self._request = request

    async def model_step(
        self,
        execution_id: str,
        turn: int,
        content: str,
        tool_calls: tuple[Mapping[str, Any], ...],
        stop_reason: str,
        input_context: Mapping[str, Any] | None,
    ) -> None:
        self._check(execution_id)
        await self._memory.record_model_message(
            session_id=self._request.session_id,
            text=content,
            tool_calls=[dict(item) for item in tool_calls],
            stop_reason=stop_reason,
            input_context=dict(input_context) if input_context is not None else None,
        )

    async def tool_result(
        self,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> None:
        self._check(execution_id)
        await self._memory.record_tool_result(
            session_id=self._request.session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result.display_result,
            error=result.error,
            original_output=result.output,
            metadata=result.metadata,
        )

    async def authorize_tool_intent(
        self,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        side_effect_class: str,
    ) -> None:
        from common.types import Event, EventType

        self._check(execution_id)
        await self._memory.session_store.append_events(
            self._request.session_id,
            Event(
                session_id=self._request.session_id,
                event_type=EventType.TOOL_CALL,
                content={
                    "execution_id": execution_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "side_effect_class": side_effect_class,
                    "intent_authorized": True,
                },
            ),
        )

    def _check(self, execution_id: str) -> None:
        if execution_id != self._request.execution_id:
            raise ValueError("cross-execution QQ audit write rejected")


class ReplyServiceQQEventSinkFactory:
    """Preserve legacy group-density and tool-hint progress behavior."""

    def __init__(self, reply_service: Any) -> None:
        self._reply = reply_service

    def for_execution(
        self, execution_id: str, user_message: dict[str, Any]
    ) -> "ReplyServiceQQEventSink":
        return ReplyServiceQQEventSink(self._reply, user_message)


@dataclass(frozen=True)
class _QQToolCall:
    name: str


class ReplyServiceQQEventSink:
    def __init__(self, reply_service: Any, user_message: dict[str, Any]) -> None:
        self._reply = reply_service
        self._user_message = user_message
        self._closed = False

    async def progress(self, phase: str, summary: str) -> None:
        # QQ does not mirror Web's generic progress stream.
        return None

    async def tool_progress(self, tool_names: tuple[str, ...]) -> None:
        if self._closed:
            return
        await self._reply.send_progress(
            [_QQToolCall(name) for name in tool_names], self._user_message
        )

    async def close(self) -> None:
        self._closed = True
