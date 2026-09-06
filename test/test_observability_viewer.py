from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _load_module():
    path = Path(__file__).parents[1] / "scripts" / "observability-viewer.py"
    spec = importlib.util.spec_from_file_location("observability_viewer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


viewer = _load_module()


def test_live_polling_does_not_replace_focused_detail_filters() -> None:
    html = viewer.HTML
    assert "if(!force&&detailControlActive()){pendingDetailRender=true;return}" in html
    assert "if(!force&&signature===detailSignature)return" in html
    assert "rawFilters={component:c,status:s,event:" in html
    assert "if(pendingDetailRender&&!detailControlActive())" in html


def test_live_polling_is_bounded_and_pauses_when_hidden() -> None:
    html = viewer.HTML
    assert "POLL_INTERVAL_MS=2500" in html
    assert "FETCH_TIMEOUT_MS=8000" in html
    assert "if(document.hidden||pollInFlight)return" in html
    assert "finally{pollInFlight=false;schedulePoll()}" in html
    assert "document.addEventListener('visibilitychange'" in html
    assert "setInterval(poll" not in html


def _write(path: Path, *records: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _log(event: str, **fields: Any) -> dict[str, Any]:
    return {
        "ts": fields.pop("ts", "2026-08-12T01:00:00.000Z"),
        "level": fields.pop("level", "INFO"), "event": event,
        "component": fields.pop("component", "agent"), **fields,
    }


def test_jsonl_incremental_tail_and_cursor(tmp_path: Path) -> None:
    path = tmp_path / "hpagent.jsonl"
    _write(path, _log("agent_execution_started", execution_id="e1"))
    reader = viewer.IncrementalJsonlReader(tmp_path)

    cursor, first = reader.after()
    assert cursor == 1
    assert [event.event for event in first] == ["agent_execution_started"]

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_log("agent_execution_completed", execution_id="e1")) + "\n")
    next_cursor, second = reader.after(cursor)
    assert next_cursor == 2
    assert [event.event for event in second] == ["agent_execution_completed"]


def test_jsonl_rotation_and_truncation_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "hpagent.jsonl"
    _write(path, _log("first", execution_id="e1"))
    reader = viewer.IncrementalJsonlReader(tmp_path)
    reader.scan()

    rotated = tmp_path / "hpagent.jsonl.1"
    path.rename(rotated)
    _write(path, _log("after_rotation_with_a_long_record", execution_id="e2", padding="x" * 200))
    assert [event.event for event in reader.scan()] == ["after_rotation_with_a_long_record"]

    _write(path, _log("after_truncate", execution_id="e3"))
    assert [event.event for event in reader.scan()] == ["after_truncate"]


def test_jsonl_reader_is_bounded_and_keeps_raw(tmp_path: Path) -> None:
    path = tmp_path / "web-api.jsonl"
    _write(path, *[_log(f"event_{index}", run_id="run-1", custom=index) for index in range(5)])
    reader = viewer.IncrementalJsonlReader(tmp_path, max_events=3)
    reader.scan()
    assert len(reader.events) == 3
    assert reader.events[-1].raw["custom"] == 4
    assert reader.events[-1].surface == "web"


def test_lifecycle_pairing_uses_correlation_dimensions() -> None:
    records = [
        _log("model_call_started", execution_id="e1", component="model", turn=1, ts="2026-08-12T01:00:00.000Z"),
        _log("model_call_completed", execution_id="e1", component="model", turn=1, status="success", elapsed_ms=125, ts="2026-08-12T01:00:00.125Z"),
        _log("tool_execution_failed", execution_id="e1", component="tool", turn=1, tool="shell", tool_call_id="c1", status="failed", error_code="tool_timeout", elapsed_ms=500, ts="2026-08-12T01:00:00.500Z"),
    ]
    events = [viewer.ObservationEvent.from_json(record, "test.jsonl", index) for index, record in enumerate(records, 1)]
    nodes = viewer.pair_lifecycle(events)
    assert nodes[0]["event"] == "model_call"
    assert nodes[0]["elapsed_ms"] == 125
    assert nodes[1]["tool_call_id"] == "c1"
    assert nodes[1]["error_code"] == "tool_timeout"


def test_qq_reader_maps_turn_segment_from_execution_user_event(tmp_path: Path) -> None:
    wal = tmp_path / "active"
    workspace = tmp_path / "workspace"
    path = wal / "session-1.wal"
    _write(
        path,
        {"event_id": "u1", "session_id": "session-1", "timestamp": 1, "event_type": "user_message", "content": {"content": "hello", "account_id": "a1"}, "metadata": {"execution_id": "qq-turn-1"}},
        {"event_id": "m1", "session_id": "session-1", "timestamp": 2, "event_type": "model_message", "content": {"text": "hi"}, "metadata": {}},
        {"event_id": "u2", "session_id": "session-1", "timestamp": 3, "event_type": "user_message", "content": {"content": "next", "account_id": "a1"}, "metadata": {"execution_id": "qq-turn-2"}},
    )
    reader = viewer.QQSessionReader(wal, workspace)
    first = reader.snapshot("qq-turn-1")
    assert first["source"] == "wal"
    assert [event["event_id"] for event in first["events"]] == ["u1", "m1"]
    assert first["summary"] == "hello"


def test_qq_archived_history_takes_precedence_over_wal(tmp_path: Path) -> None:
    wal = tmp_path / "active"
    workspace = tmp_path / "workspace"
    record = {"event_id": "u1", "session_id": "session-1", "timestamp": 1, "event_type": "user_message", "content": {"content": "archived", "account_id": "a1"}, "metadata": {"execution_id": "qq-turn-1"}}
    _write(wal / "session-1.wal", {**record, "content": {"content": "wal", "account_id": "a1"}})
    _write(workspace / "a1/sessions/session-1/history.jsonl", record)
    snapshot = viewer.QQSessionReader(wal, workspace).snapshot("qq-turn-1")
    assert snapshot["source"] == "history.jsonl"
    assert snapshot["summary"] == "archived"


def test_qq_reader_throttles_unchanged_file_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = viewer.QQSessionReader(tmp_path / "active", tmp_path / "workspace")
    file_checks = 0

    def files() -> list[tuple[Path, str, str | None]]:
        nonlocal file_checks
        file_checks += 1
        return []

    monkeypatch.setattr(reader, "_files", files)
    reader.refresh()
    reader.refresh()
    assert file_checks == 1


class _FakeConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, query: str, params: Any = None):
        self.queries.append(query)
        if "FROM hpagent.runs WHERE" in query:
            return SimpleNamespace(fetchone=lambda: {"run_id": "r1", "trigger_message_id": "m1", "session_id": "s1", "conversation_id": "c1", "status": "running"})
        if "FROM hpagent.messages" in query:
            return SimpleNamespace(fetchall=lambda: [{"message_id": "m1", "status": "accepted"}])
        if "FROM hpagent.sessions" in query:
            return SimpleNamespace(fetchone=lambda: {"session_id": "s1", "status": "active"})
        if "FROM hpagent.conversations" in query:
            return SimpleNamespace(fetchone=lambda: {"conversation_id": "c1", "status": "active"})
        if "FROM hpagent.workflow_executions" in query:
            return SimpleNamespace(fetchall=lambda: [{"status": "running"}])
        if "FROM hpagent.outbox_events" in query:
            return SimpleNamespace(fetchall=lambda: [{"status": "processed"}])
        raise AssertionError(query)


def test_postgres_snapshot_mapping_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _FakeConnection()
    reader = viewer.PostgresReader("postgresql://example")
    monkeypatch.setattr(reader, "_connect", lambda: connection)
    snapshot = reader.snapshot("r1")
    assert snapshot["run"]["status"] == "running"
    assert snapshot["messages"][0]["message_id"] == "m1"
    assert all(not query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for query in connection.queries)


def test_postgres_snapshot_uses_short_lived_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _FakeConnection()
    reader = viewer.PostgresReader("postgresql://example")
    connect_count = 0

    def connect() -> _FakeConnection:
        nonlocal connect_count
        connect_count += 1
        return connection

    monkeypatch.setattr(reader, "_connect", connect)
    assert reader.snapshot("r1") == reader.snapshot("r1")
    assert connect_count == 1


def test_observatory_merges_web_logs_and_authoritative_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path / "hpagent.jsonl",
        _log("agent_execution_started", run_id="r1", execution_id="r1", status="started"),
        _log("agent_execution_completed", run_id="r1", execution_id="r1", status="success", ts="2026-08-12T01:00:01.000Z"),
    )
    postgres = viewer.PostgresReader(None)
    monkeypatch.setattr(postgres, "recent_runs", lambda: [{
        "run_id": "r1", "workflow_id": "wf1", "session_id": "s1", "account_id": "a1",
        "conversation_id": "c1", "created_at": "2026-08-12T01:00:00Z",
        "updated_at": "2026-08-12T01:00:02Z", "status": "completed", "trigger_content": "hello",
    }])
    observatory = viewer.Observatory(
        viewer.IncrementalJsonlReader(tmp_path), postgres,
        viewer.QQSessionReader(tmp_path / "wal", tmp_path / "workspace"),
    )
    execution = observatory.executions()[0]
    assert execution["trace_key"] == "r1"
    assert execution["status"] == "success"
    assert execution["authoritative_status"] == "completed"


def test_execution_detail_has_per_trace_memory_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path / "hpagent.jsonl",
        *[_log(f"event_{index}", execution_id="e1", ts=f"2026-08-12T01:00:{index:02d}.000Z") for index in range(5)],
    )
    postgres = viewer.PostgresReader(None)
    monkeypatch.setattr(postgres, "recent_runs", lambda: [])
    monkeypatch.setattr(postgres, "snapshot", lambda _: None)
    observatory = viewer.Observatory(
        viewer.IncrementalJsonlReader(tmp_path), postgres,
        viewer.QQSessionReader(tmp_path / "wal", tmp_path / "workspace"),
        max_events_per_execution=3,
    )
    detail = observatory.detail("e1")
    assert [event["event"] for event in detail["events"]] == ["event_2", "event_3", "event_4"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("running", "running"), ("completed", "success"), ("failed", "failed"), ("cancelled", "cancelled"), ("degraded", "degraded")],
)
def test_status_normalization(raw: str, expected: str) -> None:
    assert viewer.normalize_status(raw, []) == expected


def test_durable_correlation_fields_reach_waterfall() -> None:
    records = [
        _log("model_decision_started", component="model", operation_id="op-1", activity_attempt=2),
        _log("model_decision_completed", component="model", operation_id="op-1",
             result_ref="decision:op-1", stop_reason="end_turn", tool_count=0),
    ]
    events = [viewer.ObservationEvent.from_json(record, "test.jsonl", index)
              for index, record in enumerate(records, 1)]
    node = viewer.pair_lifecycle(events)[0]
    assert node["operation_id"] == "op-1"
    assert node["result_ref"] == "decision:op-1"
    assert node["activity_attempt"] == 2
    assert node["raw_event_sequence"] == 2


class _DebugConnection:
    def __init__(self, transcript: dict[str, Any] | None = None) -> None:
        self.transcript = transcript
        self.queries: list[tuple[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, query: str, params: Any = None):
        self.queries.append((query, params))
        if "FROM hpagent.agent_transcripts" in query:
            return SimpleNamespace(fetchone=lambda: self.transcript)
        if "FROM hpagent.agent_transcript_events" in query:
            return SimpleNamespace(fetchall=lambda: [{"sequence": 1, "event_type": "context"}])
        if "FROM hpagent.agent_operations" in query:
            return SimpleNamespace(fetchall=lambda: [{"operation_id": "op-1", "status": "uncertain"}])
        raise AssertionError(query)


def test_durable_debug_snapshot_is_bounded_and_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _DebugConnection({"transcript_id": "transcript-1"})
    reader = viewer.PostgresReader("postgresql://example")
    monkeypatch.setattr(reader, "_connect", lambda: connection)
    snapshot = reader.durable_debug_snapshot("run-1")
    assert snapshot["available"] is True
    assert snapshot["durable"]["events"][0]["event_type"] == "context"
    assert snapshot["durable"]["operations"][0]["status"] == "uncertain"
    assert all(query.lstrip().startswith("SELECT") and params for query, params in connection.queries)


def test_durable_debug_snapshot_allows_no_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = viewer.PostgresReader("postgresql://example")
    monkeypatch.setattr(reader, "_connect", lambda: _DebugConnection())
    snapshot = reader.durable_debug_snapshot("run-1")
    assert snapshot["durable"]["transcript"] is None
    assert snapshot["durable"]["events"] == []
    assert snapshot["durable"]["operations"]


def test_durable_debug_snapshot_degrades_on_database_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = viewer.PostgresReader("postgresql://example")
    def fail():
        raise OSError("database down")
    monkeypatch.setattr(reader, "_connect", fail)
    snapshot = reader.durable_debug_snapshot("run-1")
    assert snapshot["reason"] == "database_unavailable"
    assert "database down" in reader.last_error
