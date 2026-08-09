from __future__ import annotations

import json
import logging
import sys

from common.logging import _JsonFormatter


def test_json_formatter_promotes_structured_extra_fields() -> None:
    record = logging.makeLogRecord({
        "name": "test",
        "levelno": logging.INFO,
        "levelname": "INFO",
        "msg": "test message",
        "event": "test_event",
        "component": "test",
        "run_id": "run-123",
        "status": "success",
    })

    payload = json.loads(_JsonFormatter().format(record))

    assert payload["event"] == "test_event"
    assert payload["component"] == "test"
    assert payload["run_id"] == "run-123"
    assert payload["status"] == "success"
    assert payload["msg"] == "test message"
    assert "." in payload["ts"]


def test_json_formatter_serializes_exception_traceback() -> None:
    formatter = _JsonFormatter()
    try:
        raise TimeoutError("timed out")
    except TimeoutError:
        record = logging.getLogger("test").makeRecord(
            "test", logging.ERROR, __file__, 1, "model failed", (), exc_info=sys.exc_info(),
            extra={"event": "model_call_failed", "component": "model", "run_id": "run-123"},
        )

    payload = json.loads(formatter.format(record))

    assert payload["error"]["type"] == "TimeoutError"
    assert payload["error"]["message"] == "timed out"
    assert "Traceback" in payload["error"]["stack"]
    assert payload["run_id"] == "run-123"
