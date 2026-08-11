"""Turn memory service.

This service is the narrow memory-facing port used by execution adapters. It wraps
SessionStore details so the turn layer does not need to know every persistence
method directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from common.types import Event, EventType
from session.store import SessionStore


class TurnMemoryService:
    """Memory/event facade for a single conversation turn."""

    def __init__(self, *, session_store: SessionStore) -> None:
        self._session = session_store

    @property
    def session_store(self) -> SessionStore:
        """Compatibility access for code that still needs the underlying store."""
        return self._session

    async def ensure_session(self, session_id: str, account_id: str, channel_type: str) -> None:
        existing = await self._session.get_session(session_id)
        if existing is None:
            await self._session.create_session(
                session_id=session_id,
                account_id=account_id,
                channel_type=channel_type,
            )

    async def get_session_account(self, session_id: str) -> str:
        session = await self._session.get_session(session_id)
        return session.account_id if session else ""

    async def record_user_message(
        self,
        *,
        session_id: str,
        content: str,
        sender_id: str,
        channel_type: str,
        account_id: str,
        metadata: Dict[str, Any],
    ) -> Event:
        event = Event(
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            content={
                "content": content,
                "sender_id": sender_id,
                "channel_type": channel_type,
                "account_id": account_id,
            },
            metadata=metadata,
        )
        await self._session.append_events(session_id, event)
        return event

    async def record_model_message(
        self,
        *,
        session_id: str,
        text: str,
        tool_calls: List[Dict[str, Any]],
        stop_reason: str,
        input_context: Optional[Dict[str, Any]] = None,
    ) -> Event:
        content: Dict[str, Any] = {
            "text": text,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
        }
        if input_context is not None:
            content["input_context"] = input_context
        event = Event(
            session_id=session_id,
            event_type=EventType.MODEL_MESSAGE,
            content=content,
        )
        await self._session.append_events(session_id, event)
        return event

    async def record_tool_result(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result: Any,
        error: Any,
        original_output: Any,
        metadata: Any,
    ) -> Event:
        event = Event(
            session_id=session_id,
            event_type=EventType.TOOL_RESULT,
            content={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result": result,
                "error": error,
                "original_output": original_output,
                "metadata": metadata,
            },
        )
        await self._session.append_events(session_id, event)
        return event

    async def load_recent_events(self, session_id: str, *, limit: int = 100) -> List[Event]:
        return await self._session.get_events(session_id, limit=limit)

    async def recall_memories(
        self,
        *,
        query: str,
        account_id: str,
        session_id: str,
        channel_type: str,
        metadata: Dict[str, Any],
        original_query: str,
        rewritten_query: str,
        hyde_input_context: Optional[List[Dict[str, str]]],
        top_n: int = 5,
    ) -> Tuple[Any, str]:
        return await self._session.recall_memories(
            query=query,
            account_id=account_id,
            session_id=session_id,
            top_n=top_n,
            tags_match="any_strict",
            query_timestamp=metadata.get("iso_timestamp", ""),
            group_id=str(metadata.get("group_id", "")),
            scope=metadata.get("detail_type", ""),
            channel_type=channel_type,
            original_query=original_query,
            rewritten_query=rewritten_query,
            hyde_input_context=hyde_input_context,
        )

    async def retain_memories(
        self,
        *,
        turn_events: List[Dict[str, Any]],
        account_id: str,
        session_id: str,
        channel_type: str,
        metadata: Dict[str, Any],
    ) -> None:
        await self._session.retain_memories(
            turn_events,
            account_id,
            session_id,
            channel_type=channel_type,
            group_id=str(metadata.get("group_id", "")),
            sender_name=metadata.get("sender_name", ""),
            iso_timestamp=metadata.get("iso_timestamp", ""),
            scope=metadata.get("detail_type", ""),
        )

    async def retain_document(
        self,
        *,
        events: List[Dict[str, Any]],
        account_id: str,
        document_id: str,
        channel_type: str,
        metadata: Dict[str, Any],
        session_id: str = "",
    ) -> int:
        """用显式幂等 document_id 保留长期记忆（QQ per-execution retain）。"""
        return await self._session.retain_document(
            events,
            account_id,
            document_id,
            channel_type=channel_type,
            group_id=str(metadata.get("group_id", "")),
            sender_name=metadata.get("sender_name", ""),
            iso_timestamp=metadata.get("iso_timestamp", ""),
            scope=metadata.get("detail_type", ""),
            session_id=session_id,
            metadata=metadata,
        )

    async def archive_events(self, session_id: str) -> List[Dict[str, Any]]:
        return await self._session.archive(session_id)

    async def delete_wal(self, session_id: str) -> None:
        await self._session.delete_wal(session_id)

    async def reflect(self, account_id: str) -> int:
        return await self._session.reflect(account_id)

    async def get_metrics(self) -> Dict[str, Any]:
        if self._session._hindsight:
            return self._session._hindsight.get_metrics()
        return {}

    def maybe_log_metrics(self, turns_taken: int) -> None:
        if turns_taken > 0 and turns_taken % 10 == 0 and self._session._hindsight:
            self._session._hindsight.log_metrics()
