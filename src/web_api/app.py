from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, cast
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uuid6 import uuid7

from common.logging import log_event
from persistence.uow import UnitOfWork
from web_artifacts.services import ArtifactService
from web_domain.errors import (
    ConversationBusy,
    DomainError,
    IdempotencyConflict,
    ResourceNotFound,
    RunNotCancellable,
    RunNotRetryable,
    VersionConflict,
)
from web_domain.services import CommandResult, CommandService

from .auth import (
    AuthContext,
    AuthService,
    ConfiguredPasswordCredentialAdapter,
    CredentialAdapter,
)
from .config import WebApiSettings
from .fake_executor import FakeArtifactExecutor, FakeRunExecutor
from .models import (
    CreateArtifactRequest,
    CreateArtifactVersionRequest,
    CreateConversationRequest,
    EmptyRequest,
    LoginRequest,
    RenameConversationRequest,
    SendMessageRequest,
)
from .queries import QueryService
from .security import CursorCodec, CursorError
from .sse import SSEGateway, load_run_snapshot
from .terminal_publisher import TerminalEventPublisher

ETAG_PATTERN = re.compile(r'^"conversation-([0-9a-f-]+)-m([1-9][0-9]*)"$')
logger = logging.getLogger("HpAgent.WebApi")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, default_limit: int = 64 * 1024):
        self.app = app
        self.default_limit = default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        limit = 160 * 1024 if path.endswith("/messages") else self.default_limit
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
        response = JSONResponse(
            status_code=413,
            content={"error": {"code": "message_too_large", "message": "请求内容过大。", "request_id": scope.get("state", {}).get("request_id", "unknown"), "retryable": False, "details": {}}},
        )
        await response(scope, receive, send)


class BodyTooLarge(Exception):
    pass


class CommonHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Any]]):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied) else f"req_{uuid7()}"
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
            is_sse = request.url.path.endswith("/events")
            if not is_sse and "application/json" not in accept and "*/*" not in accept:
                return _error(request, 406, "not_acceptable", "不支持请求的响应类型。")
            if is_sse and "text/event-stream" not in accept and "*/*" not in accept:
                return _error(request, 406, "not_acceptable", "不支持请求的响应类型。")
        if request.method in {"POST", "PATCH", "PUT"} and request.url.path != "/api/v1/auth/logout":
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return _error(request, 415, "unsupported_media_type", "请求必须使用 JSON。")
        return await call_next(request)


def _error(request: Request, status: int, code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", "unknown"), "retryable": retryable, "details": details or {}}},
    )


def _etag(conversation: dict[str, Any]) -> str:
    return f'"conversation-{conversation["conversation_id"]}-m{conversation["metadata_version"]}"'


def _normalize_title(value: str | None) -> str:
    if value is None:
        return "新的对话"
    title = value.strip()
    if not title or len(title) > 200:
        raise ValueError("invalid title")
    return title


def _normalize_content(value: str) -> str:
    content = value.replace("\r\n", "\n").replace("\r", "\n")
    if not content.strip():
        raise EmptyMessage()
    if len(content) > 32_000 or len(content.encode("utf-8")) > 128 * 1024:
        raise MessageTooLarge()
    return content


def _request_origin(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        return origin
    referer = request.headers.get("referer", "")
    if not referer:
        return ""
    parsed = urlsplit(referer)
    return f"{parsed.scheme}://{parsed.netloc}"


class EmptyMessage(Exception):
    pass


class MessageTooLarge(Exception):
    pass


def create_app(
    settings: WebApiSettings | None = None,
    credential_adapter: CredentialAdapter | None = None,
) -> FastAPI:
    settings = settings or WebApiSettings.from_env()
    # The `__Host-` cookie prefix is only valid with Secure/HTTPS; non-secure
    # environments (local dev / E2E over http) use a plain name so the browser
    # accepts the session cookie.
    cookie_name = "__Host-hpagent_session" if settings.cookie_secure else "hpagent_session"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        api_pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        api_pool.wait()
        app.state.api_pool = api_pool
        app.state.auth = AuthService(api_pool, settings)
        app.state.commands = CommandService(api_pool)
        app.state.artifacts = ArtifactService(api_pool)
        app.state.queries = QueryService(
            api_pool,
            CursorCodec(
                settings.cursor_signing_keys,
                settings.active_cursor_key_id,
                settings.cursor_ttl_seconds,
            ),
        )
        # Phase E: SSE Gateway + Terminal Event Publisher over the raw Web Run
        # Redis channel.  Redis is optional: without it the SSE Gateway degrades
        # to snapshot + stream.degraded(redis_unavailable) and the client polls.
        redis_client = None
        if settings.redis_url:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
            app.state.redis_client = redis_client
        app.state.sse_gateway = SSEGateway(api_pool, settings, redis_client)
        publisher = None
        if redis_client is not None:
            publisher = TerminalEventPublisher(api_pool, redis_client, settings)
            publisher.start()
            app.state.terminal_publisher = publisher
        fake = None
        fake_artifact = None
        if settings.fake_executor_enabled:
            worker_pool = ConnectionPool(
                settings.worker_database_url or "",
                min_size=1,
                max_size=4,
                kwargs={"row_factory": dict_row},
                open=True,
            )
            worker_pool.wait()
            app.state.worker_pool = worker_pool
            fake = FakeRunExecutor(worker_pool, settings, redis_client)
            fake.start()
            fake_artifact = FakeArtifactExecutor(worker_pool, settings)
            fake_artifact.start()
        try:
            yield
        finally:
            if fake:
                if fake_artifact:
                    await fake_artifact.stop()
                await fake.stop()
                app.state.worker_pool.close()
            if publisher:
                await publisher.stop()
            if redis_client is not None:
                await redis_client.aclose()
            api_pool.close()

    app = FastAPI(
        title="HpAgent Web API",
        version="0.3",
        lifespan=lifespan,
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.credentials = credential_adapter or ConfiguredPasswordCredentialAdapter(
        settings.credential_records
    )
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(ProtocolMiddleware)
    app.add_middleware(CommonHeadersMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if any(item.get("type") == "json_invalid" for item in exc.errors()):
            return _error(request, 400, "malformed_request", "JSON 请求格式错误。")
        details = {"fields": [".".join(str(part) for part in item["loc"][1:]) for item in exc.errors()]}
        return _error(request, 422, "validation_error", "请求字段无效。", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return _error(request, 404, "resource_not_found", "资源不存在。")
        if exc.status_code == 405:
            return _error(request, 405, "method_not_allowed", "请求方法不受支持。")
        return _error(request, exc.status_code, "malformed_request", "请求无法处理。")

    @app.exception_handler(CursorError)
    async def cursor_error(request: Request, exc: CursorError):
        return _error(request, 400, exc.code, "分页游标无效或已过期。", retryable=exc.code == "cursor_expired")

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        mapping: dict[type[DomainError], tuple[int, str, str, bool]] = {
            ResourceNotFound: (404, "resource_not_found", "资源不存在。", False),
            ConversationBusy: (409, "conversation_busy", "当前对话仍有请求正在执行。", True),
            IdempotencyConflict: (409, "idempotency_conflict", "幂等键对应的请求不一致。", False),
            RunNotCancellable: (409, "run_not_cancellable", "当前运行状态不可取消。", False),
            RunNotRetryable: (409, "run_not_retryable", "当前运行状态不可重试。", False),
            VersionConflict: (412, "version_conflict", "对话版本已经变化。", True),
        }
        status, code, message, retryable = mapping.get(type(exc), (500, "service_unavailable", "服务暂不可用。", True))
        details = {"current_version": exc.current_version} if isinstance(exc, VersionConflict) else {}
        return _error(request, status, code, message, retryable=retryable, details=details)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        logger.exception("Unhandled Web API exception", extra={
            "event": "web_request_failed", "component": "web_api",
            "request_id": getattr(request.state, "request_id", None),
            "run_id": request.path_params.get("run_id"), "path": request.url.path,
            "method": request.method, "status": "failed",
        })
        return _error(request, 503, "service_unavailable", "服务暂不可用。", retryable=True)

    def auth_context(request: Request) -> AuthContext:
        raw = request.cookies.get(cookie_name)
        context = request.app.state.auth.authenticate(raw) if raw else None
        if not context:
            raise Unauthenticated()
        return cast(AuthContext, context)

    @app.exception_handler(Unauthenticated)
    async def unauthenticated(request: Request, exc: Unauthenticated):
        return _error(request, 401, "unauthenticated", "登录状态无效。", retryable=True)

    def csrf_guard(request: Request, context: AuthContext = Depends(auth_context)) -> AuthContext:
        source = _request_origin(request)
        submitted = request.headers.get("x-csrf-token", "")
        if source != settings.public_origin or not hmac.compare_digest(submitted, context.csrf_token):
            raise CsrfInvalid()
        return context

    @app.exception_handler(CsrfInvalid)
    async def csrf_invalid(request: Request, exc: CsrfInvalid):
        return _error(request, 403, "csrf_invalid", "CSRF 校验失败。", retryable=True)

    def idempotency_key(request: Request) -> str:
        values = request.headers.getlist("idempotency-key")
        if len(values) != 1:
            raise InvalidIdempotencyKey()
        value = values[0]
        try:
            parsed = UUID(value)
        except ValueError:
            raise InvalidIdempotencyKey() from None
        if str(parsed) != value.lower():
            raise InvalidIdempotencyKey()
        return value.lower()

    @app.exception_handler(InvalidIdempotencyKey)
    async def invalid_key(request: Request, exc: InvalidIdempotencyKey):
        return _error(request, 422, "validation_error", "Idempotency-Key 必须是规范 UUID。")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(request: Request) -> dict[str, str]:
        with UnitOfWork(request.app.state.api_pool) as uow:
            uow.execute("SELECT 1")
        return {"status": "ready"}

    @app.post("/auth/login")
    def login(payload: LoginRequest, request: Request):
        subject = request.app.state.credentials.verify(payload.username, payload.password)
        context = request.app.state.auth.login(subject) if subject else None
        if not context:
            return _error(request, 401, "unauthenticated", "登录凭证无效。", retryable=True)
        return_to = payload.return_to
        if not return_to.startswith("/") or return_to.startswith("//") or urlsplit(return_to).scheme:
            return_to = "/"
        response = RedirectResponse(return_to, status_code=303)
        response.set_cookie(
            cookie_name,
            context.raw_session_token,
            path="/",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/auth/login", response_class=HTMLResponse)
    def login_entry(return_to: str = "/") -> str:
        safe_return = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/"
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>HpAgent 登录</title>"
            "</head><body><main><h1>HpAgent 登录</h1><p>请通过已配置的同源登录客户端提交凭证。"
            f"</p><p data-return-to='{html.escape(safe_return, quote=True)}'>登录后返回当前页面。</p></main></body></html>"
        )

    @app.get("/api/v1/me")
    def me(context: AuthContext = Depends(auth_context)) -> dict[str, Any]:
        return {
            "account": {"account_id": str(context.account_id), "status": "active", "created_at": context.account_created_at},
            "session": {"expires_at": context.expires_at, "idle_expires_at": context.idle_expires_at},
            "csrf_token": context.csrf_token,
            "capabilities": {"qq_long_term_memory_shared": True, "qq_self_service_binding": False},
        }

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request):
        raw = request.cookies.get(cookie_name)
        context = request.app.state.auth.authenticate(raw) if raw else None
        if context:
            submitted = request.headers.get("x-csrf-token", "")
            if _request_origin(request) != settings.public_origin or not hmac.compare_digest(submitted, context.csrf_token):
                raise CsrfInvalid()
            request.app.state.auth.revoke(context)
        response = Response(status_code=204)
        response.delete_cookie(cookie_name, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
        return response

    @app.post("/api/v1/conversations")
    def create_conversation(payload: CreateConversationRequest, request: Request, context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key)):
        try:
            title = _normalize_title(payload.title)
        except ValueError:
            return _error(request, 422, "validation_error", "标题无效。")
        result: CommandResult = request.app.state.commands.create_conversation(context.account_id, key, title)
        response = JSONResponse(status_code=result.response_status, content={"conversation": result.body["conversation"]})
        response.headers["Location"] = f'/api/v1/conversations/{result.body["conversation"]["conversation_id"]}'
        response.headers["ETag"] = _etag(result.body["conversation"])
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.get("/api/v1/conversations")
    def list_conversations(request: Request, limit: int = Query(30, ge=1, le=100), cursor: str | None = None, context: AuthContext = Depends(auth_context)):
        return request.app.state.queries.list_conversations(context.account_id, limit, cursor)

    @app.get("/api/v1/conversations/{conversation_id}")
    def get_conversation(conversation_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
        result = request.app.state.queries.get_conversation(context.account_id, conversation_id)
        response = JSONResponse(content=json.loads(json.dumps(result, default=str)))
        response.headers["ETag"] = _etag(result["conversation"])
        return response

    @app.patch("/api/v1/conversations/{conversation_id}")
    def rename_conversation(conversation_id: UUID, payload: RenameConversationRequest, request: Request, context: AuthContext = Depends(csrf_guard), if_match: str | None = Header(None, alias="If-Match")):
        if not if_match:
            return _error(request, 428, "precondition_required", "缺少 If-Match。", retryable=True)
        match = ETAG_PATTERN.fullmatch(if_match)
        if not match or match.group(1) != str(conversation_id):
            return _error(request, 412, "version_conflict", "ETag 与资源不匹配。", retryable=True)
        try:
            title = _normalize_title(payload.title)
        except ValueError:
            return _error(request, 422, "validation_error", "标题无效。")
        conversation = request.app.state.queries.rename_conversation(context.account_id, conversation_id, int(match.group(2)), title)
        response = JSONResponse(content={"conversation": conversation})
        response.headers["ETag"] = _etag(conversation)
        return response

    @app.get("/api/v1/conversations/{conversation_id}/messages")
    def list_messages(conversation_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100), cursor: str | None = None, context: AuthContext = Depends(auth_context)):
        return request.app.state.queries.list_messages(context.account_id, conversation_id, limit, cursor)

    @app.post("/api/v1/conversations/{conversation_id}/messages")
    def send_message(conversation_id: UUID, payload: SendMessageRequest, request: Request, context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key)):
        try:
            content = _normalize_content(payload.content)
        except EmptyMessage:
            return _error(request, 422, "empty_message", "消息不能为空。")
        except MessageTooLarge:
            return _error(request, 413, "message_too_large", "消息过长。")
        result: CommandResult = request.app.state.commands.send_message(context.account_id, conversation_id, key, content)
        run = result.body["run"]
        log_event(
            logger, logging.INFO, "web_message_accepted", "web_api",
            request_id=request.state.request_id, run_id=run["run_id"],
            conversation_id=str(conversation_id), status="success",
        )
        body = {name: result.body[name] for name in ("user_message", "assistant_message", "run", "events_url")}
        response = JSONResponse(status_code=result.response_status, content=body)
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
        return request.app.state.queries.get_run(context.account_id, run_id)

    @app.post("/api/v1/messages/{message_id}/artifacts")
    def create_artifact(message_id: UUID, payload: CreateArtifactRequest, request: Request,
                        context: AuthContext = Depends(csrf_guard),
                        key: str = Depends(idempotency_key)):
        try:
            result: CommandResult = request.app.state.artifacts.create_artifact(
                context.account_id, message_id, key, payload.instruction
            )
        except ValueError as exc:
            if str(exc) == "artifact_source_invalid":
                return _error(request, 409, "artifact_source_invalid",
                              "只能从已完成的 Assistant 消息生成 Artifact。")
            raise
        response = JSONResponse(status_code=result.response_status, content=result.body)
        response.headers["Location"] = (
            f'/api/v1/artifacts/{result.body["artifact"]["artifact_id"]}'
        )
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.get("/api/v1/messages/{message_id}/artifacts")
    def list_message_artifacts(message_id: UUID, request: Request,
                               context: AuthContext = Depends(auth_context)):
        return request.app.state.artifacts.list_for_message(context.account_id, message_id)

    @app.get("/api/v1/artifacts/{artifact_id}")
    def get_artifact(artifact_id: UUID, request: Request,
                     context: AuthContext = Depends(auth_context)):
        return request.app.state.artifacts.get_artifact(context.account_id, artifact_id)

    @app.get("/api/v1/artifacts/{artifact_id}/versions")
    def list_artifact_versions(artifact_id: UUID, request: Request,
                               context: AuthContext = Depends(auth_context)):
        return request.app.state.artifacts.list_versions(context.account_id, artifact_id)

    @app.post("/api/v1/artifacts/{artifact_id}/versions")
    def create_artifact_version(artifact_id: UUID, payload: CreateArtifactVersionRequest,
                                request: Request, context: AuthContext = Depends(csrf_guard),
                                key: str = Depends(idempotency_key)):
        try:
            result: CommandResult = request.app.state.artifacts.create_version(
                context.account_id, artifact_id, key, payload.instruction
            )
        except ValueError as exc:
            if str(exc) == "artifact_instruction_required":
                return _error(request, 422, "validation_error", "修改要求不能为空。")
            raise
        response = JSONResponse(status_code=result.response_status, content=result.body)
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.get("/api/v1/artifact-versions/{artifact_version_id}")
    def get_artifact_version(artifact_version_id: UUID, request: Request,
                             context: AuthContext = Depends(auth_context)):
        return request.app.state.artifacts.get_version(
            context.account_id, artifact_version_id
        )

    @app.get("/api/v1/runs/{run_id}/events")
    async def run_events(
        run_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
    ):
        """SSE projection of one Run (hpagent-web-api-contract.md §12, §13).

        Authentication is the same-origin HttpOnly Cookie (no CSRF for GET).
        Connection errors before streaming return JSON; after the stream starts,
        problems are signalled with control events (stream.degraded / auth.expired).
        """
        gateway: SSEGateway = request.app.state.sse_gateway
        # Bound concurrent SSE connections before streaming begins.
        if not await gateway.try_acquire():
            return _error(
                request,
                503,
                "service_unavailable",
                "SSE 连接数已达上限，请稍后重试。",
                retryable=True,
            )
        # Verify ownership before starting the stream so pre-stream errors can
        # still be non-200 JSON (contract §12.1).
        try:
            await asyncio.to_thread(
                load_run_snapshot, request.app.state.api_pool, context.account_id, run_id
            )
        except Exception as exc:
            await gateway.release()
            if isinstance(exc, ResourceNotFound):
                return _error(request, 404, "resource_not_found", "资源不存在。")
            raise
        raw_token = request.cookies.get(cookie_name, "")
        auth_service: AuthService = request.app.state.auth

        def auth_check(raw: str) -> bool:
            return auth_service.authenticate(raw) is not None

        return StreamingResponse(
            gateway.stream(
                context.account_id,
                run_id,
                raw_token,
                auth_check,
                acquired=True,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: UUID, payload: EmptyRequest, request: Request, context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key)):
        result: CommandResult = request.app.state.commands.cancel_run(context.account_id, run_id, key)
        response = JSONResponse(
            status_code=result.response_status,
            content={name: result.body[name] for name in ("run", "assistant_message")},
        )
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.post("/api/v1/runs/{run_id}/retry")
    def retry_run(run_id: UUID, payload: EmptyRequest, request: Request, context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key)):
        result: CommandResult = request.app.state.commands.retry_run(context.account_id, run_id, key)
        body = {name: result.body[name] for name in ("source_run_id", "assistant_message", "run", "events_url")}
        response = JSONResponse(status_code=result.response_status, content=body)
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        if result.resource_reused:
            response.headers["Resource-Reused"] = "true"
        return response

    return app


class Unauthenticated(Exception):
    pass


class CsrfInvalid(Exception):
    pass


class InvalidIdempotencyKey(Exception):
    pass


app = create_app
