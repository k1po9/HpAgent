from copy import deepcopy

from persistence.command_result import CommandResult
from web_api.command_projection import command_body


def test_http_projection_adds_links_without_mutating_persisted_result():
    body = {
        "run": {"run_id": "run-1", "source_kind": "chat"},
        "assistant_message": {"files": [
            {"file_id": "file-1", "status": "ready"},
            {"file_id": "file-2", "status": "pending"},
        ]},
    }
    original = deepcopy(body)
    result = CommandResult(202, body)
    projected = command_body(result, "run", "assistant_message", events=True)
    assert projected["events_url"] == "/api/v1/runs/run-1/events"
    assert projected["source_kind"] == projected["run"]["source_kind"] == "chat"
    assert projected["assistant_message"]["files"][0]["download_url"] == "/api/v1/files/file-1/content"
    assert projected["assistant_message"]["files"][1]["download_url"] is None
    assert result.body == original


def test_work_projection_preserves_source_without_chat_links():
    body = {"run": {"run_id": "run-2", "source_kind": "work", "work_id": "work-1"}}
    original = deepcopy(body)
    result = CommandResult(202, body)
    projected = command_body(result, "run", "assistant_message")
    assert projected == {**original, "source_kind": "work"}
    assert result.body == original
