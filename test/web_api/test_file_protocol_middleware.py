from __future__ import annotations

import httpx
from fastapi import FastAPI, Request

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

    app.add_middleware(BodyLimitMiddleware, upload_limit=limit)
    app.add_middleware(ProtocolMiddleware)
    return app


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
