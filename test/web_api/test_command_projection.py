from copy import deepcopy

from persistence.command_result import CommandResult
from web_api.command_projection import command_body


def test_http_projection_adds_links_without_mutating_persisted_result():
    body = {
        "run": {"run_id": "run-1"},
        "assistant_message": {"files": [
            {"file_id": "file-1", "status": "ready"},
            {"file_id": "file-2", "status": "pending"},
        ]},
    }
    original = deepcopy(body)
    result = CommandResult(202, body)
    projected = command_body(result, "run", "assistant_message", events=True)
    assert projected["events_url"] == "/api/v1/runs/run-1/events"
    assert projected["assistant_message"]["files"][0]["download_url"] == "/api/v1/files/file-1/content"
    assert projected["assistant_message"]["files"][1]["download_url"] is None
    assert result.body == original
