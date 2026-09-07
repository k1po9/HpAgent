from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from agent_execution.tracing.models import TraceEventNode, TraceTree
from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound, VersionConflict
from web_domain.failures import is_failure_retryable

from .security import CursorCodec, CursorError


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def conversation_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": str(row["conversation_id"]),
        "title": row["title"],
        "status": row["status"],
        "last_message_seq": row["last_message_seq"],
        "metadata_version": row["metadata_version"],
        "created_at": timestamp(row["created_at"]),
        "updated_at": timestamp(row["updated_at"]),
    }


def message_dto(
    row: dict[str, Any], files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "message_id": str(row["message_id"]),
        "conversation_id": str(row["conversation_id"]),
        "role": row["role"],
        "status": row["status"],
        "content": row["content"],
        "sequence": row["sequence"],
        "client_request_id": (
            str(row["client_request_id"]) if row["client_request_id"] else None
        ),
        "produced_by_run_id": (
            str(row["produced_by_run_id"]) if row["produced_by_run_id"] else None
        ),
        "created_at": timestamp(row["created_at"]),
        "completed_at": timestamp(row["completed_at"]),
        "files": files or [],
    }


def file_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": str(row["file_id"]), "file_name": row["display_name"],
        "purpose": row["purpose"], "status": row["status"],
        "size_bytes": row["size_bytes"],
        "download_url": (
            f"/api/v1/files/{row['file_id']}/content" if row["status"] == "ready" else None
        ),
    }


def budget_dto(
    row: dict[str, Any] | None,
    usage_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    limits, used, reserved = row["limits"], row["used"], row["reserved"]
    by_source = {
        source: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for source in ("provider", "measured", "estimated")
    }
    calls = {"settled": 0, "in_flight": 0, "unmetered": 0}
    settled_sources: set[str] = set()
    token_keys = {
        "model_input_tokens": "input_tokens",
        "model_output_tokens": "output_tokens",
        "model_total_tokens": "total_tokens",
    }
    for usage_row in usage_rows or []:
        state = str(usage_row["state"])
        dimension = str(usage_row["dimension"])
        source = usage_row.get("usage_source")
        count = int(usage_row.get("operation_count") or 0)
        if dimension == "model_calls":
            if state == "settled":
                calls["settled"] += count
            elif state == "reserved":
                calls["in_flight"] += count
            elif state == "released":
                calls["unmetered"] += count
        if state == "settled" and source in by_source and dimension in token_keys:
            by_source[source][token_keys[dimension]] += int(
                usage_row.get("actual_sum") or 0
            )
            settled_sources.add(str(source))

    if calls["unmetered"]:
        usage_state = "partial"
    elif calls["in_flight"]:
        usage_state = "in_flight"
    elif calls["settled"]:
        usage_state = "complete"
    else:
        usage_state = "none"
    token_sources = settled_sources & {"provider", "estimated"}
    if token_sources == {"provider"}:
        usage_quality = "provider"
    elif token_sources == {"estimated"}:
        usage_quality = "estimated"
    elif token_sources:
        usage_quality = "mixed"
    else:
        usage_quality = "none"

    def counter(dimension: str) -> dict[str, int]:
        return {
            "used": int(used.get(dimension, 0)),
            "reserved": int(reserved.get(dimension, 0)),
            "limit": int(limits.get(dimension, 0)),
        }

    return {
        "status": row["status"], "mode": row["mode"],
        "policy_version": row["policy_version"],
        "tokens": {
            "input": counter("model_input_tokens"),
            "output": counter("model_output_tokens"),
            "total": counter("model_total_tokens"),
        },
        "model_calls": {
            **calls,
            "total_attempts": sum(calls.values()),
            "limit": int(limits.get("model_calls", 0)),
        },
        "by_source": by_source,
        "usage_state": usage_state,
        "usage_quality": usage_quality,
        "has_estimates": "estimated" in settled_sources,
        **{
            f"{dimension}_{suffix}": int(source.get(dimension, 0))
            for dimension in ("model_total_tokens", "tool_calls", "bytes_scanned")
            for suffix, source in (("used", used), ("limit", limits))
        },
    }


def run_dto(row: dict[str, Any]) -> dict[str, Any]:
    failure = None
    if row["status"] == "failed":
        failure = {
            "code": row["failure_code"],
            "message": row["failure_message"]
            or "Agent 暂时无法完成本次请求，请稍后重试。",
            "retryable": is_failure_retryable(row["failure_code"]),
        }
    return {
        "run_id": str(row["run_id"]),
        "conversation_id": str(row["conversation_id"]),
        "session_id": str(row["session_id"]),
        "trigger_message_id": str(row["trigger_message_id"]),
        "retry_of_run_id": str(row["retry_of_run_id"]) if row["retry_of_run_id"] else None,
        "agent_strategy": str(row.get("agent_strategy") or "react"),
        "status": row["status"],
        "failure": failure,
        "version": row["version"],
        "created_at": timestamp(row["created_at"]),
        "started_at": timestamp(row["started_at"]),
        "finished_at": timestamp(row["finished_at"]),
        "updated_at": timestamp(row["updated_at"]),
    }


def trace_tree_dto(tree: TraceTree) -> dict[str, Any]:
    """Serialize the ownership-scoped Trace domain tree for the Debug Panel."""

    def event_node_dto(node: TraceEventNode) -> dict[str, Any]:
        event = node.event
        return {
            "event": {
                "trace_event_id": str(event.trace_event_id),
                "trace_run_id": str(event.trace_run_id),
                "parent_event_id": (
                    str(event.parent_event_id) if event.parent_event_id else None
                ),
                "event_type": event.event_type,
                "name": event.name,
                "status": event.status,
                "started_at": timestamp(event.started_at),
                "ended_at": timestamp(event.ended_at),
                "duration_ms": event.duration_ms,
                "metadata": dict(event.metadata),
            },
            "children": [event_node_dto(child) for child in node.children],
        }

    run = tree.run
    return {
        "run": {
            "trace_run_id": str(run.trace_run_id),
            "run_id": str(run.run_id),
            "conversation_id": str(run.conversation_id),
            "strategy": run.strategy,
            "status": run.status,
            "started_at": timestamp(run.started_at),
            "ended_at": timestamp(run.ended_at),
            "metadata": dict(run.metadata),
        },
        "roots": [event_node_dto(root) for root in tree.roots],
    }


class QueryService:
    def __init__(self, database: object, cursors: CursorCodec):
        self.database = database
        self.cursors = cursors

    def get_conversation(self, account_id: UUID, conversation_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            conversation = uow.execute(
                "SELECT * FROM conversations WHERE account_id=%s AND conversation_id=%s "
                "AND status='active'",
                (account_id, conversation_id),
            ).fetchone()
            if not conversation:
                raise ResourceNotFound()
            active = uow.execute(
                "SELECT r.*,m.message_id AS m_message_id,m.conversation_id AS m_conversation_id,"
                "m.role AS m_role,m.status AS m_status,m.content AS m_content,"
                "m.sequence AS m_sequence,m.client_request_id AS m_client_request_id,"
                "m.produced_by_run_id AS m_produced_by_run_id,m.created_at AS m_created_at,"
                "m.completed_at AS m_completed_at FROM runs r JOIN messages m "
                "ON m.produced_by_run_id=r.run_id WHERE r.account_id=%s "
                "AND r.conversation_id=%s AND r.status IN ('queued','running','cancelling')",
                (account_id, conversation_id),
            ).fetchone()
            return {
                "conversation": conversation_dto(conversation),
                "active_run": self._joined_snapshot(uow, active) if active else None,
            }

    def list_conversations(
        self, account_id: UUID, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        before: tuple[datetime, UUID] | None = None
        if cursor:
            body = self.cursors.decode(cursor)
            if (
                body.get("v") != 1
                or body.get("endpoint") != "conversations"
                or body.get("account_scope") != str(account_id)
                or body.get("limit") != limit
            ):
                raise CursorError("invalid_cursor")
            try:
                before = (datetime.fromisoformat(body["updated_at"]), UUID(body["id"]))
            except (KeyError, ValueError, TypeError):
                raise CursorError("invalid_cursor") from None
        with UnitOfWork(self.database) as uow:
            params: list[Any] = [account_id]
            condition = ""
            if before:
                condition = " AND (updated_at,conversation_id)<(%s,%s)"
                params.extend(before)
            params.append(limit + 1)
            rows = list(
                uow.execute(
                    "SELECT * FROM conversations WHERE account_id=%s AND status='active'"
                    + condition
                    + " ORDER BY updated_at DESC,conversation_id DESC LIMIT %s",
                    tuple(params),
                ).fetchall()
            )
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = self.cursors.encode(
                {
                    "v": 1,
                    "endpoint": "conversations",
                    "account_scope": str(account_id),
                    "limit": limit,
                    "updated_at": last["updated_at"].isoformat(),
                    "id": str(last["conversation_id"]),
                }
            )
        return {
            "items": [conversation_dto(row) for row in items],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    def list_messages(
        self, account_id: UUID, conversation_id: UUID, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        before_sequence: int | None = None
        if cursor:
            body = self.cursors.decode(cursor)
            if (
                body.get("v") != 1
                or body.get("endpoint") != "conversation_messages"
                or body.get("account_scope") != str(account_id)
                or body.get("resource_id") != str(conversation_id)
                or body.get("limit") != limit
                or not isinstance(body.get("before_sequence"), int)
            ):
                raise CursorError("invalid_cursor")
            before_sequence = body["before_sequence"]
        with UnitOfWork(self.database) as uow:
            conversation = uow.execute(
                "SELECT last_message_seq FROM conversations WHERE account_id=%s "
                "AND conversation_id=%s AND status='active'",
                (account_id, conversation_id),
            ).fetchone()
            if not conversation:
                raise ResourceNotFound()
            params: list[Any] = [account_id, conversation_id]
            condition = ""
            if before_sequence is not None:
                condition = " AND sequence<%s"
                params.append(before_sequence)
            params.append(limit + 1)
            rows = list(
                uow.execute(
                    "SELECT * FROM messages WHERE account_id=%s AND conversation_id=%s"
                    + condition
                    + " ORDER BY sequence DESC LIMIT %s",
                    tuple(params),
                ).fetchall()
            )
            files_by_message = self._message_files(
                uow, account_id, [row["message_id"] for row in rows]
            )
        has_more = len(rows) > limit
        selected = rows[:limit]
        next_cursor = None
        if has_more and selected:
            next_cursor = self.cursors.encode(
                {
                    "v": 1,
                    "endpoint": "conversation_messages",
                    "account_scope": str(account_id),
                    "resource_id": str(conversation_id),
                    "limit": limit,
                    "before_sequence": selected[-1]["sequence"],
                }
            )
        selected.reverse()
        return {
            "items": [
                message_dto(row, files_by_message.get(row["message_id"], []))
                for row in selected
            ],
            "next_cursor": next_cursor,
            "has_more": has_more,
            "conversation_last_message_seq": conversation["last_message_seq"],
        }

    def get_run(self, account_id: UUID, run_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT r.*,m.message_id AS m_message_id,m.conversation_id AS m_conversation_id,"
                "m.role AS m_role,m.status AS m_status,m.content AS m_content,"
                "m.sequence AS m_sequence,m.client_request_id AS m_client_request_id,"
                "m.produced_by_run_id AS m_produced_by_run_id,m.created_at AS m_created_at,"
                "m.completed_at AS m_completed_at FROM runs r JOIN messages m "
                "ON m.produced_by_run_id=r.run_id WHERE r.account_id=%s AND r.run_id=%s",
                (account_id, run_id),
            ).fetchone()
            if not row:
                raise ResourceNotFound()
            return self._joined_snapshot(uow, row)

    def rename_conversation(
        self, account_id: UUID, conversation_id: UUID, version: int, title: str
    ) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "UPDATE conversations SET title=%s,metadata_version=metadata_version+1,"
                "updated_at=now() WHERE account_id=%s AND conversation_id=%s "
                "AND status='active' AND metadata_version=%s RETURNING *",
                (title, account_id, conversation_id, version),
            ).fetchone()
            if row:
                return conversation_dto(row)
            current = uow.execute(
                "SELECT metadata_version FROM conversations WHERE account_id=%s "
                "AND conversation_id=%s AND status='active'",
                (account_id, conversation_id),
            ).fetchone()
            if not current:
                raise ResourceNotFound()
            raise VersionConflict(current["metadata_version"])

    def _joined_snapshot(self, uow: UnitOfWork, row: dict[str, Any]) -> dict[str, Any]:
        message = {
            "message_id": row["m_message_id"],
            "conversation_id": row["m_conversation_id"],
            "role": row["m_role"],
            "status": row["m_status"],
            "content": row["m_content"],
            "sequence": row["m_sequence"],
            "client_request_id": row["m_client_request_id"],
            "produced_by_run_id": row["m_produced_by_run_id"],
            "created_at": row["m_created_at"],
            "completed_at": row["m_completed_at"],
        }
        files = self._message_files(
            uow, row["account_id"], [row["m_message_id"]]
        ).get(row["m_message_id"], [])
        budget = uow.execute(
            "SELECT * FROM run_budgets WHERE run_id=%s", (row["run_id"],)
        ).fetchone()
        usage_rows = uow.execute(
            "SELECT state,usage_source,dimension,"
            "SUM(COALESCE(actual_amount,0)) AS actual_sum,"
            "SUM(reserved_amount) AS reserved_sum,"
            "COUNT(DISTINCT operation_id) AS operation_count "
            "FROM run_usage_ledger WHERE run_id=%s AND dimension IN "
            "('model_input_tokens','model_output_tokens','model_total_tokens','model_calls') "
            "GROUP BY state,usage_source,dimension",
            (row["run_id"],),
        ).fetchall()
        run = run_dto(row)
        run["budget"] = budget_dto(budget, usage_rows)
        return {"run": run, "assistant_message": message_dto(message, files)}

    @staticmethod
    def _message_files(
        uow: UnitOfWork, account_id: UUID, message_ids: list[UUID],
    ) -> dict[UUID, list[dict[str, Any]]]:
        result: dict[UUID, list[dict[str, Any]]] = {}
        if not message_ids:
            return result
        rows = uow.execute(
            "SELECT mf.message_id,sf.* FROM message_files mf JOIN stored_files sf "
            "ON sf.account_id=mf.account_id AND sf.conversation_id=mf.conversation_id "
            "AND sf.file_id=mf.file_id WHERE mf.account_id=%s "
            "AND mf.message_id=ANY(%s) ORDER BY mf.message_id,mf.ordinal",
            (account_id, message_ids),
        ).fetchall()
        for row in rows:
            result.setdefault(row["message_id"], []).append(file_dto(row))
        return result
