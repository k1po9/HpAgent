from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web_api.error_handlers import register_exception_handlers
from web_api.security import CursorCodec


@pytest.mark.parametrize(
    ("kind", "status", "code", "retryable"),
    [
        ("valid", 200, None, None),
        ("tampered", 400, "invalid_cursor", False),
        ("expired", 400, "cursor_expired", True),
    ],
)
def test_cursor_http_contract(monkeypatch, kind, status, code, retryable):
    codec = CursorCodec({"test": b"cursor-test-signing-key"}, "test", ttl_seconds=60)
    monkeypatch.setattr("web_api.security.time.time", lambda: 1000)
    cursor = codec.encode({"endpoint": "conversations"})
    if kind == "tampered":
        payload, signature = cursor.split(".", 1)
        # Preserve valid JSON and change signature bytes to exercise HMAC rejection.
        signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        cursor = f"{payload}.{signature}"
    elif kind == "expired":
        monkeypatch.setattr("web_api.security.time.time", lambda: 1061)

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/page")
    def page(cursor: str):
        return codec.decode(cursor)

    with TestClient(app) as client:
        response = client.get("/page", params={"cursor": cursor})
    assert response.status_code == status
    if code is None:
        assert response.json() == {
            "endpoint": "conversations", "kid": "test", "issued_at": 1000,
        }
    else:
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["retryable"] is retryable
