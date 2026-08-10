from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from common.logging import _JsonFormatter, setup_logging


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


def test_setup_logging_supports_service_specific_files(tmp_path: Path) -> None:
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        setup_logging(
            log_dir=tmp_path,
            json_filename="web-api.jsonl",
            error_log_filename="web-api-error.log",
        )
        logger = logging.getLogger("HpAgent.WebApi.Test")
        logger.info(
            "web_message_accepted",
            extra={
                "event": "web_message_accepted",
                "component": "web_api",
                "request_id": "req-123",
                "run_id": "run-123",
                "status": "success",
            },
        )
        logger.error("test error")
        for handler in root.handlers:
            handler.flush()

        payload = json.loads((tmp_path / "web-api.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert payload["event"] == "web_message_accepted"
        assert payload["component"] == "web_api"
        assert payload["request_id"] == "req-123"
        assert payload["run_id"] == "run-123"
        assert payload["status"] == "success"
        assert payload["ts"]
        assert (tmp_path / "web-api-error.log").is_file()
        assert not (tmp_path / "hpagent.jsonl").exists()
        assert not (tmp_path / "hpagent-error.log").exists()
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = original_handlers
        root.setLevel(original_level)


def test_web_api_entrypoint_uses_independent_logs_and_disables_access_log(
    monkeypatch: Any,
) -> None:
    from web_api import __main__ as entrypoint

    setup_calls: list[dict[str, Any]] = []
    uvicorn_calls: list[dict[str, Any]] = []
    app = object()
    monkeypatch.setattr(entrypoint, "setup_logging", lambda **kwargs: setup_calls.append(kwargs))
    monkeypatch.setattr(entrypoint, "create_app", lambda: app)
    monkeypatch.setattr(
        entrypoint.uvicorn,
        "run",
        lambda target, **kwargs: uvicorn_calls.append({"target": target, **kwargs}),
    )

    entrypoint.main()

    assert setup_calls[0]["json_filename"] == "web-api.jsonl"
    assert setup_calls[0]["error_log_filename"] == "web-api-error.log"
    assert uvicorn_calls == [{
        "target": app,
        "host": "0.0.0.0",
        "port": 8080,
        "access_log": False,
    }]
