from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uuid6 import uuid7

from web_api.responses import error_response


class BodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        default_limit: int = 64 * 1024,
        upload_limit: int = 128 * 1024 * 1024,
    ):
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        is_upload = bool(re.fullmatch(r"/api/v1/uploads/[0-9a-f-]+/content", path))
        limit = (
            self.upload_limit
            if is_upload
            else 160 * 1024
            if path.endswith("/messages")
            else self.default_limit
        )
        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > limit:
                await self._reject(scope, receive, send)
                return
        except ValueError:
            pass
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        is_upload = bool(re.fullmatch(r"/api/v1/uploads/[0-9a-f-]+/content", scope.get("path", "")))
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "file_too_large" if is_upload else "message_too_large",
                    "message": "请求内容过大。",
                    "request_id": scope.get("state", {}).get("request_id", "unknown"),
                    "retryable": False,
                    "details": {},
                }
            },
        )
        await response(scope, receive, send)


class BodyTooLarge(Exception):
    pass


class CommonHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Any]]):
        supplied = request.headers.get("x-request-id", "")
        request_id = (
            supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied) else f"req_{uuid7()}"
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/v1") or request.url.path.startswith("/auth"):
            if not response.headers.get("content-type", "").startswith("text/event-stream"):
                response.headers["Cache-Control"] = "no-store"
        if response.headers.get("content-type", "").startswith("application/json"):
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        return response


class ProtocolMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Any]]):
        if request.url.path.startswith("/api/v1"):
            accept = request.headers.get("accept", "*/*")
            is_sse = bool(
                re.fullmatch(
                    r"/api/v1/(?:runs/[^/]+/events|works/[^/]+/events/stream)",
                    request.url.path,
                )
            )
            if not is_sse and "application/json" not in accept and "*/*" not in accept:
                return error_response(request, 406, "not_acceptable", "不支持请求的响应类型。")
            if is_sse and "text/event-stream" not in accept and "*/*" not in accept:
                return error_response(request, 406, "not_acceptable", "不支持请求的响应类型。")
        if request.method in {"POST", "PATCH", "PUT"} and request.url.path != "/api/v1/auth/logout":
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            is_upload_content = bool(
                re.fullmatch(r"/api/v1/uploads/[0-9a-f-]+/content", request.url.path)
            )
            expected = "application/octet-stream" if is_upload_content else "application/json"
            if content_type != expected:
                message = (
                    "上传内容必须使用 application/octet-stream。"
                    if is_upload_content
                    else "请求必须使用 JSON。"
                )
                return error_response(request, 415, "unsupported_media_type", message)
        return await call_next(request)
