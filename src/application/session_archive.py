"""Session archival application service.

The service owns archival persistence and cleanup.  Temporal Activities only
decide when to invoke it; the Agent execution loop is deliberately unrelated.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("HpAgent.SessionArchive")


class SessionArchiveService:
    def __init__(
        self,
        *,
        memory_service: Any,
        action_runtime: Any,
        resource_pool: Any,
        prompts: Any,
        file_store: Any,
        group_context: Any = None,
    ) -> None:
        self._memory = memory_service
        self._actions = action_runtime
        self._model = resource_pool
        self._prompts = prompts
        self._file_store = file_store
        self._group_context = group_context

    async def get_session_account(self, session_id: str) -> str:
        return await self._memory.get_session_account(session_id)

    async def archive(self, session_id: str, account_id: str) -> dict[str, Any]:
        from session.workspace import (
            generate_session_summary,
            update_session_meta,
            write_history_jsonl,
        )

        events = await self._memory.archive_events(session_id)
        if not events:
            logger.warning("Archive: no events for session %s", session_id)
            return {"ok": False, "error": "No events to archive"}

        group_snapshot = None
        if self._group_context:
            try:
                group_id = ""
                for event in events:
                    if event.get("event_type") == "user_message":
                        group_id = str(event.get("metadata", {}).get("group_id", ""))
                        if group_id:
                            break
                if group_id:
                    group_snapshot = await self._group_context.snapshot(group_id)
                    remaining = await self._group_context.unsubscribe(group_id, session_id)
                    logger.info(
                        "Archive: group context unsubscribed group=%s session=%s remaining=%d",
                        group_id,
                        session_id,
                        remaining,
                    )
            except Exception:
                logger.warning("Archive: group context cleanup failed for %s", session_id)

        tool_calls = 0
        tools_used: set[str] = set()
        for event in events:
            if event.get("event_type") != "model_message":
                continue
            for tool_call in event.get("content", {}).get("tool_calls", []):
                tool_calls += 1
                tools_used.add(tool_call.get("name", ""))

        try:
            write_history_jsonl(self._file_store, account_id, session_id, events)
        except Exception as exc:
            logger.error("Archive: history.jsonl write failed for %s: %s", session_id, exc)
            return {"ok": False, "error": f"history.jsonl write failed: {exc}"}

        self._actions.clear_session(session_id)
        await self._memory.delete_wal(session_id)

        task_summary = ""
        tags: list[str] = []
        try:
            task_summary, tags = await generate_session_summary(
                events, self._model, self._prompts
            )
        except Exception as exc:
            logger.warning("Archive: summary generation failed for %s: %s", session_id, exc)

        try:
            update_session_meta(
                self._file_store,
                account_id,
                session_id,
                status="completed",
                task_summary=task_summary,
                tags=list(tags),
                event_count=len(events),
                tool_calls=tool_calls,
                tools_used=sorted(tools_used),
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **(dict(group_context=group_snapshot) if group_snapshot else {}),
            )
        except Exception as exc:
            logger.warning("Archive: meta.yaml update failed for %s: %s", session_id, exc)

        logger.info(
            "Archive complete: %s (%d events, %d tool calls, tags=%s)",
            session_id,
            len(events),
            tool_calls,
            tags,
        )
        return {
            "ok": True,
            "task_summary": task_summary,
            "tags": tags,
            "event_count": len(events),
            "tool_calls": tool_calls,
            "tools_used": sorted(tools_used),
        }
