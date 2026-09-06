#!/usr/bin/env python3
"""HpAgent Execution Observatory (P0).

Read-only local viewer for structured JSONL logs, Web PostgreSQL state, and
QQ WAL/history.  It deliberately has no business write endpoints and no
Temporal/Redis/Hindsight live dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TERMINAL_EVENTS = {
    "agent_execution_completed", "agent_execution_failed", "run_completed",
    "run_failed", "run_cancelled",
}
START_SUFFIXES = ("_started", "_starting")
END_SUFFIXES = ("_completed", "_failed", "_cancelled", "_degraded", "_skipped")


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e12:
            number /= 1000
        return number
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return float(value)
            except ValueError:
                pass
    return 0.0


def _iso_ts(epoch: float) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _event_base(name: str) -> str:
    for suffix in START_SUFFIXES + END_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


@dataclass
class ObservationEvent:
    ts: str
    ts_epoch: float
    level: str = "INFO"
    logger: str = ""
    event: str = "unknown"
    component: str = "unknown"
    status: str | None = None
    surface: str | None = None
    request_id: str | None = None
    run_id: str | None = None
    execution_id: str | None = None
    workflow_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    account_id: str | None = None
    tool_call_id: str | None = None
    tool: str | None = None
    operation_id: str | None = None
    result_ref: str | None = None
    activity_attempt: int | None = None
    stop_reason: str | None = None
    tool_count: int | None = None
    turn: int | None = None
    phase: str | None = None
    elapsed_ms: float | None = None
    error_code: str | None = None
    source_file: str = ""
    sequence: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict[str, Any], source_file: str, sequence: int) -> "ObservationEvent":
        epoch = _parse_ts(raw.get("ts") or raw.get("timestamp"))
        execution_id = _string(raw.get("execution_id"))
        run_id = _string(raw.get("run_id"))
        surface = _string(raw.get("surface"))
        if not surface:
            if run_id:
                surface = "web"
            elif execution_id and execution_id.startswith("qq-turn-"):
                surface = "qq"
        return cls(
            ts=str(raw.get("ts") or _iso_ts(epoch)), ts_epoch=epoch,
            level=str(raw.get("level") or "INFO"), logger=str(raw.get("logger") or ""),
            event=str(raw.get("event") or "unknown"), component=str(raw.get("component") or "unknown"),
            status=_string(raw.get("status")), surface=surface,
            request_id=_string(raw.get("request_id")), run_id=run_id,
            execution_id=execution_id, workflow_id=_string(raw.get("workflow_id")),
            conversation_id=_string(raw.get("conversation_id")), session_id=_string(raw.get("session_id")),
            account_id=_string(raw.get("account_id")), tool_call_id=_string(raw.get("tool_call_id")),
            tool=_string(raw.get("tool")), operation_id=_string(raw.get("operation_id")),
            result_ref=_string(raw.get("result_ref")),
            activity_attempt=_integer(raw.get("activity_attempt")),
            stop_reason=_string(raw.get("stop_reason")), tool_count=_integer(raw.get("tool_count")),
            turn=_integer(raw.get("turn")), phase=_string(raw.get("phase")),
            elapsed_ms=_number(raw.get("elapsed_ms")), error_code=_string(raw.get("error_code")),
            source_file=source_file, sequence=sequence, raw=raw,
        )

    @property
    def trace_key(self) -> str | None:
        return self.execution_id or self.run_id or self.workflow_id


def _string(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class _TailState:
    inode: int
    offset: int = 0
    partial: bytes = b""


class IncrementalJsonlReader:
    """Incrementally tails every JSONL file with rotation/truncation handling."""

    def __init__(self, log_dir: Path, max_events: int = 30_000) -> None:
        self.log_dir = log_dir
        self.events: deque[ObservationEvent] = deque(maxlen=max_events)
        self.states: dict[Path, _TailState] = {}
        self.sequence = 0
        self.parse_errors = 0
        self._lock = threading.Lock()

    def scan(self) -> list[ObservationEvent]:
        added: list[ObservationEvent] = []
        with self._lock:
            paths = set(self.log_dir.glob("*.jsonl")) if self.log_dir.exists() else set()
            for missing in set(self.states) - paths:
                del self.states[missing]
            for path in sorted(paths):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                state = self.states.get(path)
                if state is None or state.inode != stat.st_ino or stat.st_size < state.offset:
                    state = _TailState(stat.st_ino)
                    self.states[path] = state
                try:
                    with path.open("rb") as handle:
                        handle.seek(state.offset)
                        chunk = handle.read()
                        state.offset = handle.tell()
                except OSError:
                    continue
                if not chunk:
                    continue
                data = state.partial + chunk
                lines = data.split(b"\n")
                state.partial = lines.pop()
                for encoded in lines:
                    if not encoded.strip():
                        continue
                    try:
                        raw = json.loads(encoded.decode("utf-8"))
                        if not isinstance(raw, dict):
                            raise ValueError("JSONL record is not an object")
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                        self.parse_errors += 1
                        continue
                    self.sequence += 1
                    event = ObservationEvent.from_json(raw, path.name, self.sequence)
                    self.events.append(event)
                    added.append(event)
        return added

    def after(self, cursor: int = 0) -> tuple[int, list[ObservationEvent]]:
        self.scan()
        with self._lock:
            return self.sequence, [event for event in self.events if event.sequence > cursor]


class PostgresReader:
    """On-demand, read-only PostgreSQL snapshots for Web runs."""

    def __init__(self, database_url: str | None, cache_ttl_seconds: float = 5.0) -> None:
        self.database_url = database_url
        self.cache_ttl_seconds = cache_ttl_seconds
        self.last_error: str | None = None
        self._cache_lock = threading.Lock()
        self._recent_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
        self._snapshot_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._debug_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _connect(self):
        if not self.database_url:
            raise RuntimeError("database URL is not configured")
        import psycopg
        from psycopg.rows import dict_row
        connection = psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=2)
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET statement_timeout = '3000ms'")
        connection.execute("SET lock_timeout = '1000ms'")
        return connection

    def recent_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 500)
        sql = (
            "SELECT r.*,m.content AS trigger_content FROM hpagent.runs r "
            "JOIN hpagent.messages m ON m.message_id=r.trigger_message_id "
            "ORDER BY r.updated_at DESC,r.run_id DESC LIMIT %s"
        )
        with self._cache_lock:
            now = time.monotonic()
            cached = self._recent_cache.get(limit)
            if cached and now - cached[0] < self.cache_ttl_seconds:
                return cached[1]
            try:
                with self._connect() as connection:
                    rows = connection.execute(sql, (limit,)).fetchall()
                result = [dict(row) for row in rows]
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                result = []
            self._recent_cache[limit] = (time.monotonic(), result)
            return result

    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        with self._cache_lock:
            now = time.monotonic()
            cached = self._snapshot_cache.get(run_id)
            if cached and now - cached[0] < self.cache_ttl_seconds:
                return cached[1]
            try:
                with self._connect() as connection:
                    run = connection.execute(
                        "SELECT * FROM hpagent.runs WHERE run_id=%s", (run_id,),
                    ).fetchone()
                    if run is None:
                        result = None
                    else:
                        messages = connection.execute(
                            "SELECT * FROM hpagent.messages WHERE message_id=%s OR produced_by_run_id=%s "
                            "ORDER BY sequence", (run["trigger_message_id"], run_id),
                        ).fetchall()
                        session = connection.execute(
                            "SELECT * FROM hpagent.sessions WHERE session_id=%s", (run["session_id"],),
                        ).fetchone()
                        conversation = connection.execute(
                            "SELECT * FROM hpagent.conversations WHERE conversation_id=%s", (run["conversation_id"],),
                        ).fetchone()
                        workflows = connection.execute(
                            "SELECT * FROM hpagent.workflow_executions WHERE run_id=%s ORDER BY execution_sequence", (run_id,),
                        ).fetchall()
                        outbox = connection.execute(
                            "SELECT * FROM hpagent.outbox_events WHERE run_id=%s ORDER BY created_at,outbox_event_id", (run_id,),
                        ).fetchall()
                        result = {
                            "run": dict(run), "messages": [dict(row) for row in messages],
                            "session": dict(session) if session else None,
                            "conversation": dict(conversation) if conversation else None,
                            "workflow_executions": [dict(row) for row in workflows],
                            "outbox_events": [dict(row) for row in outbox],
                        }
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                result = None
            if len(self._snapshot_cache) >= 256:
                oldest = min(self._snapshot_cache, key=lambda key: self._snapshot_cache[key][0])
                del self._snapshot_cache[oldest]
            self._snapshot_cache[run_id] = (time.monotonic(), result)
            return result

    def durable_debug_snapshot(
        self, run_id: str, *, max_events: int = 1000, max_operations: int = 1000,
    ) -> dict[str, Any]:
        """Return bounded durable Agent evidence without enlarging polled snapshots."""
        max_events = min(max(max_events, 1), 1000)
        max_operations = min(max(max_operations, 1), 1000)
        with self._cache_lock:
            now = time.monotonic()
            cached = self._debug_cache.get(run_id)
            if cached and now - cached[0] < self.cache_ttl_seconds:
                return cached[1]
            try:
                with self._connect() as connection:
                    transcript = connection.execute(
                        "SELECT transcript_id,run_id,account_id,conversation_id,session_id,"
                        "schema_version,version,created_at,updated_at "
                        "FROM hpagent.agent_transcripts WHERE run_id=%s", (run_id,),
                    ).fetchone()
                    events: list[Any] = []
                    if transcript is not None:
                        events = connection.execute(
                            "SELECT transcript_id,sequence,event_type,operation_id,payload,created_at "
                            "FROM hpagent.agent_transcript_events WHERE transcript_id=%s "
                            "ORDER BY sequence LIMIT %s", (transcript["transcript_id"], max_events + 1),
                        ).fetchall()
                    operations = connection.execute(
                        "SELECT operation_id,run_id,operation_type,status,result_ref,result_payload,"
                        "error_code,attempt_count,started_at,completed_at,updated_at "
                        "FROM hpagent.agent_operations WHERE run_id=%s "
                        "ORDER BY started_at,operation_id LIMIT %s", (run_id, max_operations + 1),
                    ).fetchall()
                result = {
                    "available": True,
                    "run_id": run_id,
                    "durable": {
                        "transcript": dict(transcript) if transcript else None,
                        "events": [dict(row) for row in events[:max_events]],
                        "operations": [dict(row) for row in operations[:max_operations]],
                        "truncated": {
                            "events": len(events) > max_events,
                            "operations": len(operations) > max_operations,
                        },
                    },
                }
                self.last_error = None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self.last_error = error
                result = {"available": False, "reason": "database_unavailable", "error": error}
            if len(self._debug_cache) >= 256:
                oldest = min(self._debug_cache, key=lambda key: self._debug_cache[key][0])
                del self._debug_cache[oldest]
            self._debug_cache[run_id] = (time.monotonic(), result)
            return result


class QQSessionReader:
    """mtime-cached reader for active WAL and archived history.jsonl."""

    def __init__(self, wal_dir: Path, workspace_dir: Path) -> None:
        self.wal_dir = wal_dir
        self.workspace_dir = workspace_dir
        self._stamp: tuple[tuple[str, int, int], ...] = ()
        self._executions: dict[str, dict[str, Any]] = {}
        self._refresh_lock = threading.Lock()
        self._last_refresh_check = 0.0
        self.last_error: str | None = None

    def _files(self) -> list[tuple[Path, str, str | None]]:
        files: list[tuple[Path, str, str | None]] = []
        archived: set[str] = set()
        if self.workspace_dir.exists():
            for path in self.workspace_dir.glob("*/sessions/*/history.jsonl"):
                session_id = path.parent.name
                account_id = path.parents[2].name
                files.append((path, "history.jsonl", account_id))
                archived.add(session_id)
        if self.wal_dir.exists():
            for path in self.wal_dir.glob("*.wal"):
                if path.stem not in archived and not path.stem.startswith("reflect-"):
                    files.append((path, "wal", None))
        return files

    def refresh(self) -> dict[str, dict[str, Any]]:
        with self._refresh_lock:
            now = time.monotonic()
            if now - self._last_refresh_check < 1.0:
                return self._executions
            self._last_refresh_check = now
            return self._refresh_files()

    def _refresh_files(self) -> dict[str, dict[str, Any]]:
        files = self._files()
        stamp = tuple(sorted((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path, _, _ in files))
        if stamp == self._stamp:
            return self._executions
        executions: dict[str, dict[str, Any]] = {}
        try:
            for path, source, inferred_account in files:
                records = _read_json_lines(path)
                boundaries: list[tuple[int, str]] = []
                for index, record in enumerate(records):
                    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
                    execution_id = _string(metadata.get("execution_id"))
                    if record.get("event_type") == "user_message" and execution_id:
                        boundaries.append((index, execution_id))
                for position, (start, execution_id) in enumerate(boundaries):
                    end = boundaries[position + 1][0] if position + 1 < len(boundaries) else len(records)
                    segment = records[start:end]
                    first = segment[0]
                    content = first.get("content") if isinstance(first.get("content"), dict) else {}
                    metadata = first.get("metadata") if isinstance(first.get("metadata"), dict) else {}
                    executions[execution_id] = {
                        "execution_id": execution_id, "session_id": str(first.get("session_id") or path.stem),
                        "account_id": str(content.get("account_id") or inferred_account or ""),
                        "surface": "qq", "source": source, "source_path": str(path),
                        "events": segment, "started_at": _iso_ts(_parse_ts(first.get("timestamp"))),
                        "activity_at": _iso_ts(max((_parse_ts(item.get("timestamp")) for item in segment), default=0)),
                        "summary": str(content.get("content") or "")[:180],
                    }
            self._stamp = stamp
            self._executions = executions
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        return self._executions

    def executions(self) -> list[dict[str, Any]]:
        return list(self.refresh().values())

    def snapshot(self, execution_id: str) -> dict[str, Any] | None:
        return self.refresh().get(execution_id)


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        records.append(record)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records


def normalize_status(raw: str | None, events: list[ObservationEvent]) -> str:
    status = (raw or "").lower()
    if status in {"failed", "error", "timed_out", "terminated"}:
        return "failed"
    if status in {"cancelled", "canceled", "aborted"}:
        return "cancelled"
    if status in {"completed", "success", "succeeded"}:
        return "success"
    if status in {"queued", "running", "started", "processing", "cancelling", "retrying"}:
        return "running"
    if status in {"degraded", "skipped"}:
        return "degraded"
    if any(event.status == "failed" or event.level == "ERROR" for event in events):
        return "failed"
    if any(event.status == "degraded" for event in events):
        return "degraded"
    if any(event.event.endswith("_completed") for event in events):
        return "success"
    return "unknown"


def pair_lifecycle(events: list[ObservationEvent]) -> list[dict[str, Any]]:
    """Pair start/end lifecycle observations without hiding raw events."""
    pending: dict[tuple[Any, ...], list[ObservationEvent]] = {}
    nodes: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: (item.ts_epoch, item.sequence)):
        base = _event_base(event.event)
        key = (base, event.turn, event.tool_call_id, event.phase, event.raw.get("operation"))
        if event.event.endswith(START_SUFFIXES):
            pending.setdefault(key, []).append(event)
            continue
        start = pending.get(key, []).pop(0) if event.event.endswith(END_SUFFIXES) and pending.get(key) else None
        elapsed = event.elapsed_ms
        if elapsed is None and start and event.ts_epoch and start.ts_epoch:
            elapsed = round((event.ts_epoch - start.ts_epoch) * 1000, 1)
        nodes.append({
            "component": event.component, "event": base if start else event.event,
            "started_event": start.event if start else None, "ended_event": event.event if start else None,
            "ts": start.ts if start else event.ts, "status": normalize_status(event.status, [event]),
            "elapsed_ms": elapsed, "turn": event.turn, "phase": event.phase,
            "tool": event.tool, "tool_call_id": event.tool_call_id,
            "operation_id": event.operation_id or (start.operation_id if start else None),
            "result_ref": event.result_ref or (start.result_ref if start else None),
            "activity_attempt": event.activity_attempt or (start.activity_attempt if start else None),
            "stop_reason": event.stop_reason or (start.stop_reason if start else None),
            "tool_count": event.tool_count if event.tool_count is not None else (start.tool_count if start else None),
            "raw_event_sequence": event.sequence,
            "error_code": event.error_code, "source": "OBSERVED",
        })
    for starts in pending.values():
        for event in starts:
            nodes.append({
                "component": event.component, "event": event.event, "ts": event.ts,
                "status": "running", "elapsed_ms": None, "turn": event.turn, "phase": event.phase,
                "tool": event.tool, "tool_call_id": event.tool_call_id,
                "operation_id": event.operation_id, "result_ref": event.result_ref,
                "activity_attempt": event.activity_attempt, "stop_reason": event.stop_reason,
                "tool_count": event.tool_count, "raw_event_sequence": event.sequence,
                "error_code": event.error_code, "source": "OBSERVED", "incomplete": True,
            })
    return sorted(nodes, key=lambda item: item.get("ts") or "")


class Observatory:
    def __init__(
        self,
        logs: IncrementalJsonlReader,
        postgres: PostgresReader,
        qq: QQSessionReader,
        max_events_per_execution: int = 1_500,
    ) -> None:
        self.logs = logs
        self.postgres = postgres
        self.qq = qq
        self.max_events_per_execution = max_events_per_execution

    def executions(self) -> list[dict[str, Any]]:
        _, log_events = self.logs.after(0)
        grouped: dict[str, list[ObservationEvent]] = {}
        for event in log_events:
            if event.trace_key:
                grouped.setdefault(event.trace_key, []).append(event)
        results: dict[str, dict[str, Any]] = {}
        for key, events in grouped.items():
            ordered = sorted(events, key=lambda item: (item.ts_epoch, item.sequence))
            latest = ordered[-1]
            summary = next((str(item.raw.get("summary") or item.raw.get("msg") or "") for item in ordered if item.event in {"request_received", "agent_execution_started"}), "")
            results[key] = {
                "trace_key": key, "surface": latest.surface or ("web" if latest.run_id else "qq" if key.startswith("qq-turn-") else None),
                "run_id": latest.run_id, "execution_id": latest.execution_id,
                "workflow_id": latest.workflow_id, "session_id": latest.session_id,
                "account_id": latest.account_id, "conversation_id": latest.conversation_id,
                "started_at": ordered[0].ts, "activity_at": latest.ts,
                "status": normalize_status(latest.status, ordered), "observed_status": latest.status,
                "summary": summary[:180], "event_count": len(ordered),
                "components": sorted({item.component for item in ordered}),
                "search_text": " ".join(str(value) for item in ordered for value in (
                    item.event, item.tool, item.tool_call_id, item.error_code,
                ) if value),
            }
        for run in self.postgres.recent_runs():
            key = str(run["run_id"])
            entry = results.setdefault(key, {"trace_key": key, "event_count": 0, "summary": ""})
            entry.update({
                "surface": "web", "run_id": key, "execution_id": entry.get("execution_id") or key,
                "workflow_id": str(run["workflow_id"]), "session_id": str(run["session_id"]),
                "account_id": str(run["account_id"]), "conversation_id": str(run["conversation_id"]),
                "started_at": entry.get("started_at") or _json_default(run["created_at"]),
                "activity_at": max(str(entry.get("activity_at") or ""), _json_default(run["updated_at"])),
                "status": normalize_status(str(run["status"]), grouped.get(key, [])),
                "authoritative_status": str(run["status"]),
                "summary": entry.get("summary") or str(run.get("trigger_content") or "")[:180],
            })
        for execution in self.qq.executions():
            key = execution["execution_id"]
            entry = results.setdefault(key, {"trace_key": key, "event_count": 0})
            entry.update({
                "surface": "qq", "execution_id": key,
                "session_id": entry.get("session_id") or execution["session_id"],
                "account_id": entry.get("account_id") or execution["account_id"],
                "started_at": entry.get("started_at") or execution["started_at"],
                "activity_at": max(str(entry.get("activity_at") or ""), execution["activity_at"]),
                "summary": entry.get("summary") or execution["summary"],
                "qq_source": execution["source"],
            })
            entry["status"] = entry.get("status") if entry.get("status") not in {None, "unknown"} else "unknown"
        return sorted(results.values(), key=lambda item: item.get("activity_at") or "", reverse=True)[:300]

    def detail(self, trace_key: str) -> dict[str, Any] | None:
        _, all_events = self.logs.after(0)
        events = [event for event in all_events if event.trace_key == trace_key][
            -self.max_events_per_execution:
        ]
        run_id = next((event.run_id for event in events if event.run_id), None)
        if not run_id and not trace_key.startswith("qq-turn-"):
            run_id = trace_key
        postgres = self.postgres.snapshot(run_id) if run_id else None
        qq = self.qq.snapshot(trace_key)
        if not events and postgres is None and qq is None:
            return None
        summary = next((item for item in self.executions() if item["trace_key"] == trace_key), {})
        anomalies: list[str] = []
        if postgres and postgres["run"]["status"] == "completed" and not any(
            event.event in {"sse_terminal_published", "sse_terminal_snapshot_sent"} for event in events
        ):
            anomalies.append("delivery observation missing")
        return {
            "summary": summary, "waterfall": pair_lifecycle(events),
            "events": [asdict(event) for event in sorted(events, key=lambda item: (item.ts_epoch, item.sequence))],
            "postgres": postgres, "qq_session": qq, "anomalies": anomalies,
            "sources": {
                "postgres": "AUTHORITATIVE" if postgres else None,
                "workflow_executions": "DURABLE" if postgres else None,
                "outbox": "DURABLE" if postgres else None,
                "qq_session": "DURABLE" if qq else None,
                "jsonl": "OBSERVED" if events else None,
                "waterfall": "DERIVED",
            },
        }

    def debug_detail(self, trace_key: str) -> dict[str, Any]:
        summary = next((item for item in self.executions() if item["trace_key"] == trace_key), None)
        if summary and summary.get("surface") == "qq" or trace_key.startswith("qq-turn-"):
            return {"available": False, "reason": "web_run_required"}
        _, all_events = self.logs.after(0)
        run_id = next(
            (event.run_id for event in reversed(all_events) if event.trace_key == trace_key and event.run_id),
            None,
        )
        run_id = run_id or (summary or {}).get("run_id") or trace_key
        return self.postgres.durable_debug_snapshot(str(run_id))

    def health(self) -> dict[str, Any]:
        self.logs.scan()
        self.qq.refresh()
        return {
            "status": "ok", "sources": {
                "jsonl": {"status": "ok" if self.logs.log_dir.exists() else "unavailable", "files": len(self.logs.states), "parse_errors": self.logs.parse_errors},
                "postgres": {"status": "degraded" if self.postgres.last_error else ("configured" if self.postgres.database_url else "unavailable"), "error": self.postgres.last_error},
                "qq_wal_history": {"status": "degraded" if self.qq.last_error else "ok", "executions": len(self.qq._executions), "error": self.qq.last_error},
                "temporal": {"status": "not_connected", "phase": "P1"},
                "redis": {"status": "not_connected", "phase": "P1"},
                "hindsight": {"status": "not_connected", "phase": "P1"},
            },
        }


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HpAgent Execution Observatory</title>
<style>
:root{--bg:#071018;--panel:#0d1924;--panel2:#122231;--line:#24394a;--text:#d9e7f0;--muted:#7992a5;--cyan:#4fd1c5;--green:#5bd68b;--red:#ff6b76;--amber:#ffc857;--blue:#6dafff;--purple:#bb86fc}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:13px Inter,ui-sans-serif,system-ui,sans-serif}header{height:58px;padding:0 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:20px;background:#09141e}h1{font-size:17px;margin:0;letter-spacing:.3px}.live{color:var(--green)}.health{display:flex;gap:7px;margin-left:auto}.badge,.source{border:1px solid var(--line);padding:2px 7px;border-radius:20px;font-size:10px;color:var(--muted)}.source{color:var(--cyan)}.filters{padding:10px 14px;border-bottom:1px solid var(--line);display:flex;gap:8px;background:var(--panel)}select,input{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:5px;padding:6px 8px}.filters input{flex:1}.layout{height:calc(100vh - 105px);display:grid;grid-template-columns:310px minmax(420px,1fr) 390px}.left,.main,.right{overflow:auto}.left{border-right:1px solid var(--line);padding:10px}.main{padding:14px 18px}.right{border-left:1px solid var(--line);padding:12px;background:#09141e}.card{padding:10px;border:1px solid var(--line);border-radius:7px;margin-bottom:7px;cursor:pointer;background:var(--panel)}.card:hover,.card.on{border-color:var(--cyan);background:var(--panel2)}.row{display:flex;justify-content:space-between;gap:8px}.id{font:11px ui-monospace,monospace;color:var(--blue);overflow:hidden;text-overflow:ellipsis}.summary{color:var(--muted);font-size:11px;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.status{font-weight:700;font-size:10px;text-transform:uppercase}.running,.started{color:var(--blue)}.success,.completed{color:var(--green)}.failed,.uncertain{color:var(--red)}.degraded,.intent_recorded{color:var(--amber)}.cancelled{color:var(--muted)}h2{font-size:15px;margin:0 0 10px}h3{font-size:11px;color:var(--muted);letter-spacing:1.1px;margin:18px 0 8px}.summarybox,.state{border:1px solid var(--line);border-radius:8px;background:var(--panel);padding:12px;margin-bottom:12px}.kv{display:grid;grid-template-columns:100px 1fr;gap:4px 8px;font-size:11px}.kv b{color:var(--muted);font-weight:500}.kv span{font-family:ui-monospace,monospace;overflow-wrap:anywhere}.node{display:grid;grid-template-columns:95px 90px 1fr 85px;gap:9px;border-left:3px solid var(--blue);padding:9px 10px;margin:5px 0;background:var(--panel);border-radius:0 6px 6px 0}.node.clickable{cursor:pointer}.node.clickable:hover,.node.on{background:var(--panel2);outline:1px solid var(--cyan)}.node.failed{border-color:var(--red)}.node.degraded{border-color:var(--amber)}.node .time{color:var(--muted);font:10px ui-monospace,monospace}.node .component{font-weight:700;text-transform:uppercase}.node .detail{color:var(--text)}.node .duration{text-align:right;color:var(--cyan);font-family:ui-monospace,monospace}.tabs button{background:transparent;color:var(--muted);border:0;border-bottom:2px solid transparent;padding:7px;cursor:pointer}.tabs button.on{color:var(--cyan);border-color:var(--cyan)}pre{white-space:pre-wrap;word-break:break-word;background:#050b10;border:1px solid var(--line);padding:9px;border-radius:6px;font:10px ui-monospace,monospace;max-height:360px;overflow:auto}.raw{border-top:1px solid var(--line);padding:7px 0}.raw summary{cursor:pointer;color:var(--blue)}.empty{color:var(--muted);padding:40px;text-align:center}.anomaly{border:1px solid #6b4d1b;background:#2b2110;color:var(--amber);padding:8px;border-radius:6px;margin:6px 0}.warning{border:1px solid var(--amber);color:var(--amber);padding:9px;border-radius:6px;margin:8px 0}.timeline{border-left:2px solid var(--line);padding:6px 8px;margin:4px 0;cursor:pointer}.timeline:hover{border-color:var(--cyan);background:var(--panel2)}
.state>summary{display:flex;align-items:center;justify-content:space-between;cursor:pointer}.copy-json{background:var(--panel2);color:var(--cyan);border:1px solid var(--line);border-radius:5px;padding:3px 7px;cursor:pointer}.tool-summary.failed{border-color:var(--red)}.semantic-failed{color:var(--red);font-weight:800;margin-left:8px}.tool-error{color:var(--red);font-size:11px;margin-top:4px;overflow-wrap:anywhere}.json-key{color:var(--blue)}.json-string{color:var(--green)}.json-number{color:var(--purple)}.json-bool{color:var(--amber);font-weight:700}.json-null{color:var(--muted);font-style:italic}
</style></head><body><header><h1>HpAgent Execution Observatory</h1><span class="live">LIVE ●</span><div class="health" id="health"></div></header><div class="filters"><select id="surface"><option value="">All surfaces</option><option value="web">Web</option><option value="qq">QQ</option></select><select id="status"><option value="">All statuses</option><option>running</option><option>success</option><option>failed</option><option>degraded</option><option>cancelled</option></select><select id="component"><option value="">All components</option><option>web_api</option><option>run</option><option>dispatcher</option><option>temporal</option><option>context</option><option>agent</option><option>model</option><option>tool</option><option>memory</option><option>sse</option></select><input id="search" placeholder="Search execution / run / workflow / session / tool / error"></div><div class="layout"><aside class="left" id="list"></aside><main class="main" id="main"><div class="empty">Select an execution</div></main><aside class="right" id="state"><div class="empty">State Inspector</div></aside></div>
<script>
let executions=[],selected=null,detail=null,debugDetail=null,debugLoading=false,selectedNodeIndex=null,selectedTranscriptSequence=null,detailSignature='',pendingDetailRender=false,pollTimer=null,pollInFlight=false;let rawFilters={component:'',status:'',event:''};const POLL_INTERVAL_MS=2500,FETCH_TIMEOUT_MS=8000;const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const short=s=>{s=String(s||'');return s.length>48?s.slice(0,25)+'…'+s.slice(-12):s};
async function get(url){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),FETCH_TIMEOUT_MS);try{const r=await fetch(url,{signal:controller.signal});if(!r.ok)throw Error(r.status);return await r.json()}finally{clearTimeout(timer)}}
function detailControlActive(){const el=document.activeElement,main=document.getElementById('main');return !!(el&&main.contains(el)&&el.matches('input,select,button'))}
function applyDetail(next,{force=false}={}){const signature=JSON.stringify(next);if(!force&&signature===detailSignature)return;detail=next;detailSignature=signature;if(!force&&detailControlActive()){pendingDetailRender=true;return}pendingDetailRender=false;renderDetail()}
function schedulePoll(delay=POLL_INTERVAL_MS){clearTimeout(pollTimer);if(!document.hidden)pollTimer=setTimeout(poll,delay)}
async function poll(){if(document.hidden||pollInFlight)return;pollInFlight=true;try{const [e,h]=await Promise.all([get('/api/executions'),get('/api/health')]);executions=e.executions;renderList();renderHealth(h);if(selected)applyDetail(await get('/api/execution/'+encodeURIComponent(selected))) }catch(e){document.getElementById('health').innerHTML='<span class="badge failed">viewer degraded</span>'}finally{pollInFlight=false;schedulePoll()}}
function renderHealth(h){document.getElementById('health').innerHTML=Object.entries(h.sources).slice(0,3).map(([k,v])=>`<span class="badge ${v.status==='ok'||v.status==='configured'?'success':'degraded'}">${esc(k)} ${esc(v.status)}</span>`).join('')}
function renderList(){const surface=document.getElementById('surface').value,status=document.getElementById('status').value,component=document.getElementById('component').value,q=document.getElementById('search').value.toLowerCase();const list=executions.filter(x=>(!surface||x.surface===surface)&&(!status||x.status===status)&&(!component||(x.components||[]).includes(component))&&(!q||JSON.stringify(x).toLowerCase().includes(q)));document.getElementById('list').innerHTML=`<h2>Executions <span class="badge">${list.length}</span></h2>`+list.map(x=>`<div class="card ${selected===x.trace_key?'on':''}" onclick="choose('${esc(x.trace_key)}')"><div class="row"><b>${esc((x.surface||'?').toUpperCase())}</b><span class="status ${esc(x.status)}">${esc(x.status)}</span></div><div class="id">${esc(short(x.trace_key))}</div><div class="summary">${esc(x.summary||x.activity_at||'')}</div></div>`).join('')}
async function choose(key){selected=key;selectedNodeIndex=null;selectedTranscriptSequence=null;debugDetail=null;debugLoading=true;rawFilters={component:'',status:'',event:''};renderList();const normal=get('/api/execution/'+encodeURIComponent(key));loadDebug(key);applyDetail(await normal,{force:true})}
async function loadDebug(key){try{const loaded=await get('/api/execution/'+encodeURIComponent(key)+'/debug');if(selected===key)debugDetail=loaded}catch(e){if(selected===key)debugDetail={available:false,reason:'database_unavailable',error:String(e)}}finally{if(selected===key){debugLoading=false;renderDetail()}}}
function renderDetail(){if(!detail)return;const s=detail.summary||{};const ids=[['execution',s.execution_id],['run',s.run_id],['workflow',s.workflow_id],['account',s.account_id],['session',s.session_id],['conversation',s.conversation_id]].filter(x=>x[1]);document.getElementById('main').innerHTML=`<div class="summarybox"><div class="row"><h2>${esc((s.surface||'?').toUpperCase())} Execution</h2><span class="status ${esc(s.status)}">${esc(s.status)}</span></div><div class="kv">${ids.map(x=>`<b>${x[0]}</b><span>${esc(x[1])}</span>`).join('')}</div></div>${(detail.anomalies||[]).map(x=>`<div class="anomaly">STATE DIVERGENCE · ${esc(x)}</div>`).join('')}<h2>Execution Waterfall <span class="source">DERIVED</span></h2><div>${detail.waterfall.length?detail.waterfall.map(nodeHtml).join(''):'<div class="empty">No correlated JSONL lifecycle events</div>'}</div><div class="tabs"><button class="on">Raw Events</button></div><div class="filters"><select id="rawcomponent"><option value="">All components</option>${[...new Set(detail.events.map(e=>e.component))].sort().map(x=>`<option>${esc(x)}</option>`).join('')}</select><select id="rawstatus"><option value="">All outcomes</option><option value="failed">failed</option><option value="degraded">degraded</option></select><input id="rawevent" placeholder="Filter event name"></div><div id="rawlist"></div>`;document.getElementById('rawcomponent').value=rawFilters.component;document.getElementById('rawstatus').value=rawFilters.status;document.getElementById('rawevent').value=rawFilters.event;['rawcomponent','rawstatus','rawevent'].forEach(id=>document.getElementById(id).addEventListener(id==='rawevent'?'input':'change',renderRaw));renderRaw();renderInspector()}
function renderRaw(){const c=document.getElementById('rawcomponent').value,s=document.getElementById('rawstatus').value,q=document.getElementById('rawevent').value.toLowerCase();rawFilters={component:c,status:s,event:document.getElementById('rawevent').value};const rows=detail.events.filter(e=>(!c||e.component===c)&&(!q||e.event.toLowerCase().includes(q))&&(!s||(s==='failed'?(e.status==='failed'||e.level==='ERROR'):e.status==='degraded')));document.getElementById('rawlist').innerHTML=rows.map(e=>`<details class="raw"><summary>${esc(e.ts)} · ${esc(e.component)} · ${esc(e.event)} · ${esc(e.status||'')}</summary><button onclick="navigator.clipboard.writeText(this.nextElementSibling.textContent)">Copy JSON</button><pre>${esc(JSON.stringify(e.raw,null,2))}</pre></details>`).join('')||'<div class="empty">No matching raw events</div>'}
function toolSemanticState(n){const te=findTranscriptEvent(n.operation_id),raw=te?.payload?.raw_result;if(raw?.success===false)return {status:'failed',error:raw.error||'tool failed'};if(raw?.success===true)return {status:'success',error:null};return {status:'unknown',error:null}}
function nodeHtml(n,index){const semantic=n.component==='tool'?toolSemanticState(n):{status:'unknown',error:null};const more=[n.event,n.phase,n.tool,n.turn!=null?'turn '+n.turn:null,n.error_code].filter(Boolean).join(' · ');const linked=n.operation_id||n.result_ref||n.tool_call_id,status=semantic.status==='failed'?'failed':n.status;return `<div class="node ${esc(status)} ${linked?'clickable':''} ${selectedNodeIndex===index?'on':''}" onclick="inspectNode(${index})"><div class="time">${esc((n.ts||'').slice(11,23))}</div><div class="component">${esc(n.component)}${semantic.status==='failed'?'<span class="semantic-failed">❌ TOOL FAILED</span>':''}</div><div class="detail">${esc(more)}${semantic.error?`<div class="tool-error">${esc(semantic.error)}</div>`:''}</div><div class="duration">${n.elapsed_ms==null?'—':esc(Math.round(n.elapsed_ms)+' ms')}</div></div>`}
function inspectNode(index){selectedNodeIndex=index;selectedTranscriptSequence=null;renderDetail()}
function durable(){return debugDetail?.durable||{}}
function durableEvents(){return durable().events||[]}
function durableOperations(){return durable().operations||[]}
function findOperation(operationId,resultRef){return durableOperations().find(x=>operationId&&x.operation_id===operationId)||durableOperations().find(x=>resultRef&&x.result_ref===resultRef)||null}
function findTranscriptEvent(operationId){return durableEvents().find(x=>operationId&&x.operation_id===operationId)||null}
function findToolCall(toolCallId,beforeSequence=Infinity){for(const e of durableEvents().filter(x=>x.sequence<beforeSequence).reverse()){const calls=e.payload?.message?.tool_calls||[];const call=calls.find(x=>x.id===toolCallId);if(call)return call}return null}
function reconstructTranscriptBefore(sequence){const messages=[];for(const e of durableEvents().filter(x=>x.sequence<sequence)){if(e.event_type==='context')messages.push(...(e.payload?.messages||[]));else if(['model_decision','tool_result','system'].includes(e.event_type)&&e.payload?.message)messages.push(e.payload.message)}return messages}
function inspectTranscript(sequence){selectedNodeIndex=null;selectedTranscriptSequence=sequence;renderInspector()}
function lifecycleBox(n){const lifecycle=stateBox('Lifecycle',{status:n.status,elapsed_ms:n.elapsed_ms,activity_attempt:n.activity_attempt,stop_reason:n.stop_reason,tool_count:n.tool_count,operation_id:n.operation_id,result_ref:n.result_ref,tool_call_id:n.tool_call_id,error_code:n.error_code});return n.component==='tool'?lifecycle+toolSummaryBox(n,findOperation(n.operation_id,n.result_ref),findTranscriptEvent(n.operation_id)):lifecycle}
function toolSummaryBox(n,op,te){const semantic=toolSemanticState(n),raw=te?.payload?.raw_result||{};return `<div class="summarybox tool-summary ${semantic.status}"><div class="kv"><b>Tool</b><span>${esc(n.tool||te?.payload?.message?.name||'—')}</span><b>Invocation</b><span>${esc(op?.status||n.status||'unknown')}</span><b>Semantic Result</b><span class="status ${esc(semantic.status)}">${esc(semantic.status.toUpperCase())}</span><b>Error</b><span>${esc(semantic.error||raw.error||'—')}</span><b>Side Effect</b><span>${esc(te?.payload?.side_effect_class||op?.result_payload?.side_effect_class||'—')}</span><b>Transcript Version</b><span>${esc(op?.result_payload?.transcript_version??te?.sequence??'—')}</span></div></div>`}
function renderInspector(){if(!detail)return;const p=detail.postgres,q=detail.qq_session,src=detail.sources;let html='<h2>Debug Inspector</h2>';if(selectedNodeIndex==null&&selectedTranscriptSequence==null){html+='<h3>SOURCE BADGES</h3>'+Object.entries(src).filter(x=>x[1]).map(([k,v])=>`<div class="row"><span>${esc(k)}</span><span class="source">${esc(v)}</span></div>`).join('');if(p)html+='<h3>POSTGRESQL</h3>'+stateBox('run',p.run)+stateBox('messages',p.messages)+stateBox('workflow executions',p.workflow_executions)+stateBox('outbox',p.outbox_events);if(q)html+='<h3>QQ SESSION</h3>'+stateBox(q.source,q.events)}
if(debugLoading)html+='<div class="empty">Loading durable debug evidence…</div>';else if(debugDetail&&!debugDetail.available){html+=debugDetail.reason==='web_run_required'?'<div class="empty">Durable Agent drill-down is available for Web runs.</div>':`<div class="warning">Durable Debug Unavailable<br>${esc(debugDetail.error||debugDetail.reason)}</div>`}else if(debugDetail?.available){const d=durable(),tr=d.transcript;if(d.truncated?.events||d.truncated?.operations)html+=`<div class="warning">Showing first 1000 ${d.truncated.events?'events ':''}${d.truncated.operations?'operations':''}</div>`;if(selectedTranscriptSequence!=null){const e=durableEvents().find(x=>x.sequence===selectedTranscriptSequence);if(e)html+=`<h3>TRANSCRIPT EVENT #${esc(e.sequence)}</h3>`+stateBox(e.event_type,e)}else if(selectedNodeIndex!=null){const n=detail.waterfall[selectedNodeIndex],op=findOperation(n.operation_id,n.result_ref),te=findTranscriptEvent(n.operation_id);html+=`<h3>${esc(n.component)} · ${esc(n.event)}</h3>`+lifecycleBox(n);if(op){if(['intent_recorded','uncertain'].includes(op.status))html+=`<div class="warning">SIDE EFFECT STATE · ${esc(op.status.toUpperCase())}</div>`;html+=stateBox('Durable Operation',op)}if(n.component==='model'||(te&&te.event_type==='model_decision')){const e=te;html+='<h3>MODEL OUTPUT</h3>'+stateBox('message',e?.payload?.message||null)+stateBox('tool calls',e?.payload?.message?.tool_calls||[]);if(e)html+='<h3>DURABLE TRANSCRIPT BEFORE DECISION</h3>'+stateBox('messages',reconstructTranscriptBefore(e.sequence));if(op)html+=stateBox('Operation Result',op.result_payload);if(e)html+=stateBox('Transcript Event Payload',e.payload)}else if(n.component==='tool'||(te&&te.event_type==='tool_result')){const call=findToolCall(n.tool_call_id,te?.sequence||Infinity);html+='<h3>TOOL EXECUTION</h3>'+stateBox('arguments',call||{tool_call_id:n.tool_call_id,name:n.tool,arguments:null})+stateBox('tool result',te?.payload||null);if(op)html+=stateBox('Operation Result / Intent',op.result_payload)}else if(te)html+=stateBox('Transcript Event',te)}else if(tr){html+='<h3>TRANSCRIPT</h3>'+stateBox('identity',tr)+durableEvents().map(e=>`<div class="timeline" onclick="inspectTranscript(${Number(e.sequence)})"><b>#${esc(e.sequence)} ${esc(e.event_type)}</b><div class="id">${esc(e.operation_id||'')}</div></div>`).join('')+stateBox('operations',durableOperations())}else html+='<div class="empty">No durable transcript yet.</div>'}document.getElementById('state').innerHTML=html}
function syntaxHighlightJson(data){const json=JSON.stringify(data,null,2)??'null',token=/("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"\s*:|"(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b)/g;let out='',last=0;for(const match of json.matchAll(token)){out+=esc(json.slice(last,match.index));const value=match[0],trim=value.trim(),cls=trim.endsWith(':')?'json-key':trim.startsWith('"')?'json-string':/^(true|false)$/.test(trim)?'json-bool':trim==='null'?'json-null':'json-number';out+=`<span class="${cls}">${esc(value)}</span>`;last=match.index+value.length}return out+esc(json.slice(last))}
async function copyJson(button){const text=button.closest('.state').querySelector('pre').textContent;try{if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(text);else throw Error('clipboard unavailable')}catch(e){const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();document.execCommand('copy');area.remove()}const old=button.textContent;button.textContent='Copied ✓';setTimeout(()=>button.textContent=old,1200)}
function stateBox(title,data){return `<details class="state" open><summary><b>${esc(title)}</b><button type="button" class="copy-json" onclick="event.preventDefault();event.stopPropagation();copyJson(this)">Copy JSON</button></summary><pre>${syntaxHighlightJson(data)}</pre></details>`}
['surface','status','component','search'].forEach(id=>document.getElementById(id).addEventListener(id==='search'?'input':'change',renderList));document.addEventListener('focusout',()=>setTimeout(()=>{if(pendingDetailRender&&!detailControlActive()){pendingDetailRender=false;renderDetail()}},0));document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);if(!document.hidden)poll()});poll();
</script></body></html>'''


def make_handler(observatory: Observatory):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(HTML, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/health":
                self._json(observatory.health())
                return
            if parsed.path == "/api/executions":
                self._json({"executions": observatory.executions()})
                return
            if parsed.path.startswith("/api/execution/") and parsed.path.endswith("/debug"):
                trace_key = unquote(parsed.path[len("/api/execution/"):-len("/debug")]).rstrip("/")
                self._json(observatory.debug_detail(trace_key))
                return
            if parsed.path.startswith("/api/execution/") or parsed.path.startswith("/api/raw/"):
                trace_key = unquote(parsed.path.split("/", 3)[3])
                detail = observatory.detail(trace_key)
                if detail is None:
                    self._json({"error": "execution_not_found"}, HTTPStatus.NOT_FOUND)
                elif parsed.path.startswith("/api/raw/"):
                    self._json({"events": detail["events"]})
                else:
                    self._json(detail)
                return
            if parsed.path == "/api/events":
                try:
                    cursor = int(dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item).get("cursor", "0"))
                except ValueError:
                    cursor = 0
                next_cursor, events = observatory.logs.after(cursor)
                self._json({"cursor": str(next_cursor), "events": [asdict(event) for event in events]})
                return
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def _json(self, body: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send(json.dumps(body, ensure_ascii=False, default=_json_default), "application/json; charset=utf-8", status)

        def _send(self, body: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def _load_storage_paths(config_path: Path) -> tuple[Path, Path]:
    wal = PROJECT_ROOT / ".data/active-sessions"
    workspace = PROJECT_ROOT / ".data/workspace"
    try:
        import yaml
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        wal = _resolve_path((config.get("session") or {}).get("backup_dir", str(wal)), config_path)
        workspace = _resolve_path((config.get("workspace") or {}).get("root", str(workspace)), config_path)
    except (OSError, ImportError, ValueError):
        pass
    return wal, workspace


def _resolve_path(raw: str, config_path: Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (config_path.parent.parent / path).resolve()


def default_database_url() -> str | None:
    configured = os.getenv("OBSERVABILITY_DATABASE_URL") or os.getenv("WORKER_DATABASE_URL") or os.getenv("APP_DATABASE_URL")
    if configured:
        return configured
    password = os.getenv("HPAGENT_WORKER_PASSWORD", "hpagent_worker")
    return f"postgresql://hpagent_worker:{password}@127.0.0.1:5434/hpagent"


def build_observatory(args: argparse.Namespace) -> Observatory:
    wal, workspace = _load_storage_paths(Path(args.config))
    return Observatory(
        IncrementalJsonlReader(Path(args.log_dir), args.max_events),
        PostgresReader(args.database_url),
        QQSessionReader(wal, workspace),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="HpAgent local execution observatory (read-only P0)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config/config.yaml"))
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", str(PROJECT_ROOT / ".data/logs")))
    parser.add_argument("--database-url", default=default_database_url())
    parser.add_argument("--max-events", type=int, default=30_000)
    args = parser.parse_args()
    observatory = build_observatory(args)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(observatory))
    print(f"HpAgent Execution Observatory: http://{args.host}:{args.port}")
    print(f"  JSONL: {args.log_dir}")
    print(f"  PostgreSQL: {'configured (read-only)' if args.database_url else 'disabled'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
