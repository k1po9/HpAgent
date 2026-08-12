"""Read-only Web Context assembly with strict Conversation boundaries."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from uuid import UUID

from common.logging import log_event
from common.types import Event, EventType
from harness.context_builder import HarnessContextBuilder
from memory.hindsight_client import MemoryItem
from persistence.repositories import MessageRepository, RunRepository
from persistence.uow import UnitOfWork

logger = logging.getLogger("HpAgent.ContextAssembly")


class ContextIsolationError(RuntimeError):
    """A recall result cannot safely be proven to belong to the requested Account."""


class LongTermRecall(Protocol):
    async def recall(
        self, query: str, user_id: str, session_id: str = "", top_n: int = 5, **kwargs: Any
    ) -> Sequence[MemoryItem]: ...


@dataclass(frozen=True)
class WebContextBase:
    run_id: UUID
    account_id: UUID
    conversation_id: UUID
    session_id: UUID
    context_message_seq: int
    trigger_message_id: UUID
    trigger_content: str
    short_term_events: tuple[Event, ...]
    interaction_profile: str = "web_chat"


class ContextAssemblyService:
    """Two-phase ContextProvider for Web Runs.

    ``load_base`` never calls Hindsight.  The execution layer must first derive
    its rewrite/HyDE query and only then call ``recall_long_term``.
    """

    def __init__(
        self,
        database_url: object,
        context_builder: HarnessContextBuilder,
        hindsight: LongTermRecall | None = None,
        *,
        recall_top_n: int = 5,
        token_budget: int = 256_000,
        generation_headroom: int = 16_000,
    ) -> None:
        self._database_url = database_url
        self._builder = context_builder
        self._hindsight = hindsight
        self._recall_top_n = recall_top_n
        self._token_budget = token_budget
        self._generation_headroom = generation_headroom
        self._messages = MessageRepository()
        self._runs = RunRepository()

    def load_base(self, account_id: UUID, run_id: UUID) -> WebContextBase:
        """Load one frozen context snapshot owned by ``account_id``.

        The public method deliberately accepts no client-controlled Conversation
        or Session ID.  Those values are loaded exclusively through the Run's
        composite ownership chain.
        """
        with UnitOfWork(self._database_url) as uow:
            subject = self._runs.context_subject(uow, account_id, run_id)
            if subject is None:
                raise ContextIsolationError("run ownership chain is unavailable")
            if subject["trigger_role"] != "user" or subject["trigger_status"] != "accepted":
                raise ContextIsolationError("run trigger message is not an accepted user message")
            if subject["session_status"] not in ("active", "archiving"):
                raise ContextIsolationError("run session is not recoverable")
            rows = self._messages.context_messages(
                uow, account_id, subject["conversation_id"], subject["context_message_seq"]
            )

        events = tuple(self._message_to_event(row) for row in rows)
        return WebContextBase(
            run_id=subject["run_id"],
            account_id=subject["account_id"],
            conversation_id=subject["conversation_id"],
            session_id=subject["session_id"],
            context_message_seq=subject["context_message_seq"],
            trigger_message_id=subject["trigger_message_id"],
            trigger_content=subject["trigger_content"],
            short_term_events=events,
        )

    async def recall_long_term(
        self, base: WebContextBase, recall_query: str
    ) -> tuple[MemoryItem, ...]:
        """Recall account-bank memory only after query rewrite; unavailable is empty."""
        if self._hindsight is None:
            return ()
        started_at = time.monotonic()
        correlation = {
            "run_id": str(base.run_id),
            "execution_id": str(base.run_id),
            "conversation_id": str(base.conversation_id),
            "session_id": str(base.session_id),
            "account_id": str(base.account_id),
            "surface": "web",
        }
        log_event(
            logger, logging.INFO, "memory_recall_started", "memory",
            status="started", **correlation,
        )
        try:
            recalled = await self._hindsight.recall(
                recall_query,
                user_id=str(base.account_id),
                session_id=str(base.session_id),
                top_n=self._recall_top_n,
                channel_type="web",
            )
        except Exception:
            logger.exception("Web Hindsight recall unavailable", extra={
                "event": "memory_recall_degraded", "component": "memory",
                **correlation, "status": "degraded",
                "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                "error_code": "memory_recall_failed",
            })
            return ()
        validated: list[MemoryItem] = []
        for item in recalled:
            self._validate_memory(item, base.account_id)
            validated.append(item)
        log_event(
            logger, logging.INFO, "memory_recall_completed", "memory",
            **correlation, status="success", result_count=len(validated),
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
        )
        return tuple(validated)

    def compose(
        self, base: WebContextBase, memories: Sequence[MemoryItem]
    ) -> list[dict[str, Any]]:
        """Compose model input with existing Harness token-budget behavior."""
        return self._builder.build(
            list(base.short_term_events),
            recalled_memories=self._format_memories(memories),
            interaction_profile=base.interaction_profile,
            token_budget=self._token_budget,
            generation_headroom=self._generation_headroom,
        )

    @staticmethod
    def _message_to_event(row: dict[str, Any]) -> Event:
        if row["role"] == "user":
            return Event(
                session_id="web-context",
                event_type=EventType.USER_MESSAGE,
                content={"content": row["content"], "channel_type": "web"},
            )
        return Event(
            session_id="web-context",
            event_type=EventType.MODEL_MESSAGE,
            content={"text": row["content"]},
        )

    @staticmethod
    def _format_memories(memories: Sequence[MemoryItem]) -> str:
        if not memories:
            return ""
        return "# 相关记忆\n\n" + "\n".join(
            f"- {item.content}" for item in memories if item.content
        )

    @staticmethod
    def _validate_memory(item: object, account_id: UUID) -> None:
        if not isinstance(item, MemoryItem) or not isinstance(item.content, str):
            raise ContextIsolationError("invalid long-term memory result")
        # Future Hindsight adapters may return an explicit owner/bank.  When it
        # does, mismatches are a security error, not a degraded recall.
        explicit_account = getattr(item, "account_id", None)
        explicit_bank = getattr(item, "bank_id", None)
        expected_bank = f"hpagent-u-{account_id}"
        if explicit_account is not None and str(explicit_account) != str(account_id):
            raise ContextIsolationError("cross-account long-term memory result")
        if explicit_bank is not None and explicit_bank != expected_bank:
            raise ContextIsolationError("long-term memory bank mismatch")
