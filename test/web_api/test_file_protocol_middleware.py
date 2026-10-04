from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from web_api.app import BodyLimitMiddleware, ProtocolMiddleware


def _app(limit: int = 32) -> FastAPI:
    app = FastAPI()

    @app.put("/api/v1/uploads/{file_id}/content")
    async def upload(file_id: str, request: Request):
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
        return {"file_id": file_id, "size": size}

    @app.put("/api/v1/other")
    async def other():
        return {"ok": True}

    @app.get("/api/v1/runs/{run_id}/events")
    @app.get("/api/v1/works/{work_id}/events/stream")
    async def stream():
        return StreamingResponse(iter(["data: {}\n\n"]), media_type="text/event-stream")

    @app.get("/api/v1/works/{work_id}/events")
    async def work_events():
        return {"items": []}

    app.add_middleware(BodyLimitMiddleware, upload_limit=limit)
    app.add_middleware(ProtocolMiddleware)
    return app


@pytest.mark.parametrize("path,accept,expected", [
    ("/api/v1/runs/r/events", "text/event-stream", 200),
    ("/api/v1/runs/r/events", "application/json", 406),
    ("/api/v1/works/w/events/stream", "text/event-stream", 200),
    ("/api/v1/works/w/events/stream", "application/json", 406),
    ("/api/v1/works/w/events", "application/json", 200),
    ("/api/v1/works/w/events", "text/event-stream", 406),
    ("/api/v1/works/w/events", "*/*", 200),
    ("/api/v1/works/w/events/stream", "*/*", 200),
])
async def test_event_routes_negotiate_their_actual_response_type(path, accept, expected):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get(path, headers={"accept": accept})
    assert response.status_code == expected
    if expected == 200:
        content_type = "text/event-stream" if accept == "text/event-stream" or path.endswith("/stream") else "application/json"
        assert response.headers["content-type"].startswith(content_type)
    else:
        assert response.json()["error"]["code"] == "not_acceptable"


async def test_upload_content_has_a_narrow_octet_stream_exception() -> None:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/v1/uploads/00000000-0000-0000-0000-000000000001/content",
            content=b"log", headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200
        assert response.json()["size"] == 3

        other = await client.put(
            "/api/v1/other", content=b"raw",
            headers={"content-type": "application/octet-stream"},
        )
        assert other.status_code == 415


async def test_upload_body_limit_applies_without_trusting_route_code() -> None:
    transport = httpx.ASGITransport(app=_app(limit=3))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/v1/uploads/00000000-0000-0000-0000-000000000001/content",
            content=b"four", headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "file_too_large"
