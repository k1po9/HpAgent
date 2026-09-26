from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import re
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable, cast
from urllib.parse import quote, urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from psycopg.errors import CheckViolation, ForeignKeyViolation, RaiseException, UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uuid6 import uuid7

from account.credentials import PostgresPasswordCredentialAdapter
from account.identity_binding_service import (
    ChallengeNotFound,
    IdentityBindingService,
    mask_qq_subject,
)
from account.invite_service import InvalidRegistrationInvite
from account.registration_service import (
    InvalidPassword,
    InvalidUsername,
    RegistrationService,
    UsernameAlreadyExists,
)
from common.logging import log_event
from conversation_domain.commands import CommandService
from file_domain.approvals import ApprovalNotPending, FileActionApprovalService
from persistence.command_result import CommandResult
from persistence.migrate import verify_schema
from persistence.uow import UnitOfWork
from research_domain.models import SourceStrategy
from research_domain.services import (
    ResearchTaskCommandService,
    TaskBusy,
    TaskNotActive,
)
from storage.tenant_file_store import TenantFileStore
from tracing.repository import PostgresTraceRepository
from web_artifacts.services import ArtifactService
from web_domain.errors import (
    ConversationBusy,
    DomainError,
    FileAlreadyBound,
    FileEncodingUnsupported,
    FileHashMismatch,
    FileNotReady,
    FileTooLarge,
    FileUploadInvalid,
    IdempotencyConflict,
    ResourceNotFound,
    RunNotCancellable,
    RunNotRetryable,
    RunRetryNotSafe,
    UnsupportedFileType,
    VersionConflict,
)
from web_domain.file_services import FileService
from workspace.catalog import (
    WorkspaceCatalog,
    WorkspaceConflict,
    WorkspaceNotFound,
    WorkspaceVersionConflict,
)
from workspace.discovery import WorkspaceDiscovery
from workspace.resources import ResourceDenied, ResourcePolicy, SnapshotLimitExceeded

from .auth import (
    AuthContext,
    AuthService,
    CredentialAdapter,
)
from .command_projection import command_body
from .config import WebApiSettings
from .fake_executor import FakeArtifactExecutor, FakeRunExecutor
from .model_observability_queries import ModelInputUnavailable, ModelObservabilityQueries
from .models import (
    CreateArtifactRequest,
    CreateArtifactVersionRequest,
    CreateConversationRequest,
    CreateResearchTaskRequest,
    CreateUploadRequest,
    CreateWorkspaceDirectoryRequest,
    EmptyRequest,
    GrantConversationResourceRequest,
    LoginRequest,
    MoveWorkspaceNodeRequest,
    RegisterRequest,
    RenameConversationRequest,
    SaveWorkspaceFileRequest,
    SendMessageRequest,
    UpdateResearchOutputRequest,
    UpdateResearchScheduleRequest,
    UpdateWorkspaceFileRequest,
)
from .queries import QueryService, trace_tree_dto
from .security import CursorCodec, CursorError
from .sse import SSEGateway, load_run_snapshot
from .terminal_publisher import TerminalEventPublisher

ETAG_PATTERN = re.compile(r'^"conversation-([0-9a-f-]+)-m([1-9][0-9]*)"$')
logger = logging.getLogger("HpAgent.WebApi")


class BodyLimitMiddleware:
    def __init__(
        self, app: ASGIApp, default_limit: int = 64 * 1024,
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
            self.upload_limit if is_upload
            else 160 * 1024 if path.endswith("/messages")
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
        is_upload = bool(re.fullmatch(
            r"/api/v1/uploads/[0-9a-f-]+/content", scope.get("path", "")
        ))
        response = JSONResponse(
            status_code=413,
            content={"error": {"code": "file_too_large" if is_upload else "message_too_large", "message": "请求内容过大。", "request_id": scope.get("state", {}).get("request_id", "unknown"), "retryable": False, "details": {}}},
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
            is_upload_content = bool(re.fullmatch(
                r"/api/v1/uploads/[0-9a-f-]+/content", request.url.path
            ))
            expected = "application/octet-stream" if is_upload_content else "application/json"
            if content_type != expected:
                message = (
                    "上传内容必须使用 application/octet-stream。"
                    if is_upload_content else "请求必须使用 JSON。"
                )
                return _error(request, 415, "unsupported_media_type", message)
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
        async with AsyncExitStack() as resources:
            api_pool = ConnectionPool(
                settings.database_url,
                min_size=1,
                max_size=10,
                kwargs={"row_factory": dict_row},
                open=True,
            )
            resources.callback(api_pool.close)
            api_pool.wait()
            verify_schema(api_pool)
            app.state.api_pool = api_pool
            app.state.auth = AuthService(api_pool, settings)
            app.state.credentials = credential_adapter or PostgresPasswordCredentialAdapter(api_pool)
            app.state.registration = RegistrationService(api_pool)
            app.state.identity_bindings = IdentityBindingService(
                api_pool,
                settings.qq_binding_code_pepper,
                settings.qq_binding_challenge_seconds,
            )
            app.state.commands = CommandService(
                api_pool,
                budget_mode=settings.run_budget_mode,
                budget_policy_version=settings.run_budget_policy_version,
            )
            app.state.research_tasks = ResearchTaskCommandService(
                api_pool, budget_mode=settings.run_budget_mode
            )
            app.state.file_approvals = FileActionApprovalService(api_pool)
            app.state.workspace = WorkspaceCatalog(api_pool, app.state.commands)
            app.state.resource_policy = ResourcePolicy(api_pool)
            if settings.web_file_upload_enabled:
                file_root = Path(settings.file_store_root).resolve()
                application_root = Path.cwd().resolve()
                if file_root == application_root or file_root.is_relative_to(application_root):
                    raise RuntimeError("FILE_STORE_ROOT must be outside the application/Git workspace")
                app.state.file_service = FileService(
                    api_pool,
                    TenantFileStore(file_root, max_bytes=settings.file_max_bytes),
                    max_bytes=settings.file_max_bytes,
                )
            app.state.artifacts = ArtifactService(api_pool)
            app.state.queries = QueryService(
                api_pool,
                CursorCodec(
                    settings.cursor_signing_keys,
                    settings.active_cursor_key_id,
                    settings.cursor_ttl_seconds,
                ),
            )
            app.state.trace_repository = PostgresTraceRepository(api_pool)
            app.state.model_observability = ModelObservabilityQueries(api_pool)
            # Redis is optional; the gateway degrades to snapshot + polling.
            redis_client = None
            if settings.redis_url:
                import redis.asyncio as aioredis

                redis_client = aioredis.from_url(
                    settings.redis_url, decode_responses=False
                )
                resources.push_async_callback(redis_client.aclose)
                app.state.redis_client = redis_client
            app.state.sse_gateway = SSEGateway(api_pool, settings, redis_client)
            if redis_client is not None:
                publisher = TerminalEventPublisher(api_pool, redis_client, settings)
                publisher.start()
                resources.push_async_callback(publisher.stop)
                app.state.terminal_publisher = publisher
            if settings.fake_executor_enabled:
                worker_pool = ConnectionPool(
                    settings.worker_database_url or "",
                    min_size=1,
                    max_size=4,
                    kwargs={"row_factory": dict_row},
                    open=True,
                )
                resources.callback(worker_pool.close)
                worker_pool.wait()
                app.state.worker_pool = worker_pool
                fake = FakeRunExecutor(worker_pool, settings, redis_client)
                fake.start()
                resources.push_async_callback(fake.stop)
                fake_artifact = FakeArtifactExecutor(worker_pool, settings)
                fake_artifact.start()
                resources.push_async_callback(fake_artifact.stop)
            yield

    app = FastAPI(
        title="HpAgent Web API",
        version="0.3",
        lifespan=lifespan,
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(BodyLimitMiddleware, upload_limit=settings.file_max_bytes)
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
            WorkspaceNotFound: (404, "workspace_not_found", "Workspace 资源不存在。", False),
            WorkspaceConflict: (409, "workspace_conflict", "Workspace 操作冲突或名称无效。", False),
            ResourceDenied: (403, "resource_denied", "当前主体或 Run 无权使用该资源。", False),
            SnapshotLimitExceeded: (413, "candidate_limit_exceeded", "候选超过上限，请缩小授权范围。", False),
            ConversationBusy: (409, "conversation_busy", "当前对话仍有请求正在执行。", True),
            FileNotReady: (409, "file_not_ready", "文件尚未准备完成。", True),
            FileAlreadyBound: (409, "file_already_bound", "附件不能重复绑定。", False),
            FileTooLarge: (413, "file_too_large", "文件超过大小限制。", False),
            UnsupportedFileType: (415, "unsupported_file_type", "不支持该文件类型。", False),
            FileEncodingUnsupported: (422, "file_encoding_unsupported", "文件编码不受支持。", False),
            FileHashMismatch: (422, "file_hash_mismatch", "文件哈希校验失败。", False),
            FileUploadInvalid: (409, "file_not_ready", "上传状态无效，请重新创建上传。", False),
            IdempotencyConflict: (409, "idempotency_conflict", "幂等键对应的请求不一致。", False),
            RunNotCancellable: (409, "run_not_cancellable", "当前运行状态不可取消。", False),
            RunNotRetryable: (409, "run_not_retryable", "当前运行状态不可重试。", False),
            RunRetryNotSafe: (409, "run_retry_not_safe", "任务包含无法确认的外部操作，不能自动重试。", False),
            VersionConflict: (412, "version_conflict", "对话版本已经变化。", True),
            TaskBusy: (409, "task_busy", "该任务已有运行中的执行。", True),
            TaskNotActive: (409, "task_not_active", "该任务当前不可触发。", False),
            ApprovalNotPending: (409, "approval_not_pending", "审批请求已处理或已过期。", False),
        }
        status, code, message, retryable = mapping.get(type(exc), (500, "service_unavailable", "服务暂不可用。", True))
        details = {"current_version": exc.current_version} if isinstance(exc, VersionConflict) else {}
        if isinstance(exc, RunRetryNotSafe):
            details = {
                "failure_code": exc.failure_code,
                "reason": "unsafe_side_effect_state",
            }
        if code in {"resource_denied", "candidate_limit_exceeded", "workspace_conflict"}:
            log_event(logger, logging.WARNING, "workspace_request_rejected", "web_api",
                      code=code, request_id=getattr(request.state, "request_id", None),
                      run_id=request.path_params.get("run_id"),
                      node_id=request.path_params.get("node_id"))
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

    @app.exception_handler(ModelInputUnavailable)
    async def model_input_unavailable(request: Request, exc: ModelInputUnavailable):
        return _error(
            request, 403, "model_input_unavailable", "当前账号不可查看模型输入。"
        )

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

    def file_service(request: Request) -> FileService:
        service = getattr(request.app.state, "file_service", None)
        if service is None:
            raise ResourceNotFound()
        return cast(FileService, service)

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

    @app.post("/auth/register")
    def register(payload: RegisterRequest, request: Request):
        try:
            result = request.app.state.registration.register(
                payload.username, payload.password, payload.invite_code
            )
        except InvalidRegistrationInvite:
            return _error(request, 403, "registration_invite_invalid", "邀请码无效或不可用。")
        except UsernameAlreadyExists:
            return _error(
                request, 409, "username_already_exists", "用户名已存在。"
            )
        except InvalidUsername:
            return _error(request, 422, "invalid_username", "用户名无效。")
        except InvalidPassword:
            return _error(
                request,
                422,
                "invalid_password",
                "密码长度必须为 8 到 128 个字符。",
            )
        try:
            context = request.app.state.auth.login(result.normalized_subject)
        except Exception:
            logger.exception(
                "web_registration_session_creation_raised account_id=%s",
                result.account_id,
            )
            context = None
        if context is None:
            logger.error(
                "web_registration_session_creation_failed account_id=%s",
                result.account_id,
            )
            return JSONResponse(
                status_code=201,
                content={
                    "account": {"account_id": str(result.account_id)},
                    "registered": True,
                    "session_established": False,
                },
            )
        response = JSONResponse(
            status_code=201,
            content={
                "account": {"account_id": str(context.account_id)},
                "registered": True,
                "session_established": True,
            },
        )
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
    def me(request: Request, context: AuthContext = Depends(auth_context)) -> dict[str, Any]:
        identities = request.app.state.identity_bindings.get_identity_summary(
            context.account_id
        )
        return {
            "account": {"account_id": str(context.account_id), "status": "active", "created_at": context.account_created_at},
            "session": {"expires_at": context.expires_at, "idle_expires_at": context.idle_expires_at},
            "csrf_token": context.csrf_token,
            "identities": identities,
            "capabilities": {
                "qq_long_term_memory_shared": True,
                "qq_self_service_binding": True,
                "durable_agent": True,
                "file_upload": settings.web_file_upload_enabled,
                "file_transform": settings.web_file_transform_enabled,
                "agent_strategies": ["react", "plan_and_execute"],
            },
        }

    @app.post("/api/v1/identity-bindings/qq/challenges", status_code=201)
    def create_qq_binding_challenge(
        request: Request, context: AuthContext = Depends(csrf_guard)
    ) -> dict[str, Any]:
        challenge = request.app.state.identity_bindings.create_qq_challenge(
            context.account_id
        )
        return {
            "challenge_id": str(challenge.challenge_id),
            "code": challenge.code,
            "expires_at": challenge.expires_at,
            "instruction": f"请使用需要绑定的 QQ 向 HpAgent 发送：绑定 {challenge.code}",
        }

    @app.get("/api/v1/identity-bindings/qq/challenges/{challenge_id}")
    def get_qq_binding_challenge(
        challenge_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
    ) -> Any:
        try:
            challenge = request.app.state.identity_bindings.get_qq_challenge(
                context.account_id, challenge_id
            )
        except ChallengeNotFound:
            return _error(request, 404, "resource_not_found", "绑定验证不存在。")
        result: dict[str, Any] = {
            "challenge_id": str(challenge.challenge_id),
            "status": challenge.status,
            "expires_at": challenge.expires_at,
        }
        if challenge.status == "completed" and challenge.verified_subject_id:
            result["qq"] = {
                "bound": True,
                "display_subject": mask_qq_subject(challenge.verified_subject_id),
            }
        return result

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

    @app.get("/api/v1/tasks")
    def list_research_tasks(
        request: Request,
        before: UUID | None = None,
        context: AuthContext = Depends(auth_context),
    ):
        return request.app.state.research_tasks.list_tasks(context.account_id, before)

    @app.post("/api/v1/tasks")
    def create_research_task(
        payload: CreateResearchTaskRequest,
        request: Request,
        context: AuthContext = Depends(csrf_guard),
        key: str = Depends(idempotency_key),
    ):
        strategy = SourceStrategy.from_dict(payload.source_strategy.model_dump())
        result = request.app.state.research_tasks.create_task(
            context.account_id, key, payload.title, payload.objective, strategy,
            conversation_id=payload.conversation_id,
            output_directory_id=payload.output_directory_id,
            output_required=payload.output_required,
        )
        response = JSONResponse(status_code=result.status_code, content={"task": result.body})
        response.headers["Location"] = f'/api/v1/tasks/{result.body["task_id"]}'
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.get("/api/v1/tasks/{task_id}/runs")
    def list_research_runs(
        task_id: UUID,
        request: Request,
        before: UUID | None = None,
        context: AuthContext = Depends(auth_context),
    ):
        return request.app.state.research_tasks.list_runs(context.account_id, task_id, before)

    @app.post("/api/v1/tasks/{task_id}/runs")
    def trigger_research_task(
        task_id: UUID,
        payload: EmptyRequest,
        request: Request,
        context: AuthContext = Depends(csrf_guard),
        key: str = Depends(idempotency_key),
    ):
        del payload
        result = request.app.state.research_tasks.trigger_task(context.account_id, task_id, key)
        response = JSONResponse(status_code=result.status_code, content={"run": result.body})
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.get("/api/v1/tasks/{task_id}/resources")
    def task_resources(task_id: UUID, request: Request,
                       context: AuthContext = Depends(auth_context)):
        policy: ResourcePolicy = request.app.state.resource_policy
        return {"grants": policy.grants(context.account_id, "task", task_id)}

    @app.post("/api/v1/tasks/{task_id}/resources", status_code=201)
    def grant_task_resource(task_id: UUID, payload: GrantConversationResourceRequest,
                            request: Request, context: AuthContext = Depends(csrf_guard)):
        if set(payload.operations) - {"list_metadata", "read_content"}:
            raise ValueError("Task input grants only support read operations")
        ids = request.app.state.resource_policy.grant(
            context.account_id, "task", task_id, payload.node_id,
            payload.operations, payload.recursive,
        )
        return {"grant_ids": ids}

    @app.put("/api/v1/tasks/{task_id}/schedule")
    def update_research_schedule(
        task_id: UUID,
        payload: UpdateResearchScheduleRequest,
        request: Request,
        context: AuthContext = Depends(csrf_guard),
        key: str = Depends(idempotency_key),
    ):
        result = request.app.state.research_tasks.update_schedule(
            context.account_id,
            task_id,
            key,
            schedule_type=payload.schedule_type,
            timezone=payload.timezone,
            expression=payload.expression,
            enabled=payload.enabled,
        )
        response = JSONResponse(status_code=result.status_code, content={"schedule": result.body})
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.put("/api/v1/tasks/{task_id}/output")
    def update_research_output(
        task_id: UUID,
        payload: UpdateResearchOutputRequest,
        request: Request,
        context: AuthContext = Depends(csrf_guard),
    ):
        return {"output": request.app.state.research_tasks.update_output(
            context.account_id, task_id, payload.output_directory_id, payload.required,
            payload.operation, payload.output_entry_id,
        )}

    @app.get("/api/v1/tasks/{task_id}/runs/{run_id}")
    def get_research_run(
        task_id: UUID,
        run_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
    ):
        return {"run": request.app.state.research_tasks.get_run(
            context.account_id, task_id, run_id
        )}

    @app.get("/api/v1/tasks/{task_id}/runs/{run_id}/evidence")
    def list_research_evidence(
        task_id: UUID,
        run_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
    ):
        return {"evidence": request.app.state.research_tasks.list_evidence(
            context.account_id, task_id, run_id
        )}

    @app.get("/api/v1/tasks/{task_id}/runs/{run_id}/report")
    def get_research_report(
        task_id: UUID,
        run_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
    ):
        return {"report": request.app.state.research_tasks.get_report(
            context.account_id, task_id, run_id
        )}

    @app.get("/api/v1/conversations/{conversation_id}/file-candidates")
    def list_file_candidates(
        conversation_id: UUID,
        request: Request,
        before: UUID | None = None,
        context: AuthContext = Depends(auth_context),
        files: FileService = Depends(file_service),
    ):
        return files.list_candidates(context.account_id, conversation_id, before)

    @app.post("/api/v1/conversations/{conversation_id}/uploads")
    def create_upload(
        conversation_id: UUID,
        payload: CreateUploadRequest,
        request: Request,
        context: AuthContext = Depends(csrf_guard),
        key: str = Depends(idempotency_key),
        files: FileService = Depends(file_service),
    ):
        result = files.create_upload(
            context.account_id, conversation_id, key, payload.file_name,
            payload.size_bytes, payload.content_type, payload.sha256,
        )
        response = JSONResponse(status_code=result.response_status, content=result.body)
        response.headers["Location"] = result.body["content_url"]
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.put("/api/v1/uploads/{file_id}/content")
    async def upload_content(
        file_id: UUID,
        request: Request,
        context: AuthContext = Depends(csrf_guard),
        files: FileService = Depends(file_service),
    ):
        uploaded = await files.upload_content(
            context.account_id, file_id, request.stream()
        )
        return {"file": uploaded}

    @app.get("/api/v1/files/{file_id}")
    def get_file(
        file_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
        files: FileService = Depends(file_service),
    ):
        return {"file": files.get(context.account_id, file_id)}

    def workspace_mutation(function: Callable[[], Any]) -> Any:
        try:
            return function()
        except (CheckViolation, ForeignKeyViolation, RaiseException, UniqueViolation) as exc:
            raise WorkspaceConflict(str(exc)) from exc

    @app.get("/api/v1/workspace")
    def get_workspace(request: Request, context: AuthContext = Depends(auth_context)):
        return request.app.state.workspace.tree(context.account_id)

    @app.get("/api/v1/workspace/search")
    def search_workspace(request: Request, context: AuthContext = Depends(auth_context),
                         name: str | None = Query(default=None, max_length=255),
                         summary: str | None = Query(default=None, max_length=255),
                         content_type: str | None = None, purpose: str | None = None,
                         task_id: UUID | None = None, source_run_id: UUID | None = None,
                         from_date: date | None = None, to_date: date | None = None,
                         after: UUID | None = None,
                         limit: int = Query(default=50, ge=1, le=100)):
        return WorkspaceDiscovery(request.app.state.api_pool).search(
            context.account_id, name=name, summary=summary,
            content_type=content_type, purpose=purpose,
            task_id=task_id, source_run_id=source_run_id,
            from_date=from_date, to_date=to_date, after=after, limit=limit)

    @app.get("/api/v1/runs/{run_id}/workspace/search")
    def search_run_workspace(run_id: UUID, request: Request,
                             context: AuthContext = Depends(auth_context),
                             name: str | None = Query(default=None, max_length=255),
                             summary: str | None = Query(default=None, max_length=255),
                             content_type: str | None = None, purpose: str | None = None,
                             task_id: UUID | None = None, source_run_id: UUID | None = None,
                             from_date: date | None = None, to_date: date | None = None,
                             after: UUID | None = None,
                             limit: int = Query(default=50, ge=1, le=100)):
        return WorkspaceDiscovery(request.app.state.api_pool).search(
            context.account_id, run_id=run_id, name=name, summary=summary,
            content_type=content_type,
            purpose=purpose, task_id=task_id, source_run_id=source_run_id,
            from_date=from_date, to_date=to_date, after=after, limit=limit)

    @app.get("/api/v1/workspace/space")
    def workspace_space(request: Request, context: AuthContext = Depends(auth_context)):
        return WorkspaceDiscovery(request.app.state.api_pool).space(context.account_id)

    @app.get("/api/v1/workspace/nodes/{node_id}/trace")
    def workspace_trace(node_id: UUID, request: Request,
                        context: AuthContext = Depends(auth_context)):
        return WorkspaceDiscovery(request.app.state.api_pool).trace(context.account_id, node_id)

    @app.get("/api/v1/workspace/files/{file_id}/retention")
    def workspace_retention(file_id: UUID, request: Request,
                            context: AuthContext = Depends(auth_context)):
        return WorkspaceDiscovery(request.app.state.api_pool).retention(context.account_id, file_id)

    @app.get("/api/v1/conversations/{conversation_id}/resources")
    def conversation_resources(conversation_id: UUID, request: Request,
                               context: AuthContext = Depends(auth_context)):
        policy: ResourcePolicy = request.app.state.resource_policy
        return {"grants": policy.grants(context.account_id, "conversation", conversation_id),
                "attachments": policy.conversation_files(context.account_id, conversation_id)}

    @app.post("/api/v1/conversations/{conversation_id}/resources", status_code=201)
    def grant_conversation_resource(conversation_id: UUID,
                                    payload: GrantConversationResourceRequest,
                                    request: Request,
                                    context: AuthContext = Depends(csrf_guard)):
        ids = request.app.state.resource_policy.grant(
            context.account_id, "conversation", conversation_id,
            payload.node_id, payload.operations, payload.recursive
        )
        return {"grant_ids": ids}

    @app.delete("/api/v1/conversations/{conversation_id}/resources/{grant_id}")
    def revoke_conversation_resource(conversation_id: UUID, grant_id: UUID,
                                     request: Request,
                                     context: AuthContext = Depends(csrf_guard)):
        policy: ResourcePolicy = request.app.state.resource_policy
        if not any(item["grant_id"] == str(grant_id) for item in policy.grants(
            context.account_id, "conversation", conversation_id
        )):
            raise ResourceNotFound()
        affected = policy.revoke(context.account_id, grant_id, request.app.state.commands)
        with UnitOfWork(request.app.state.api_pool) as uow:
            statuses = {row["run_id"]: row["status"] for row in uow.execute(
                "SELECT run_id,status FROM runs WHERE account_id=%s AND run_id=ANY(%s)",
                (context.account_id, affected),
            ).fetchall()}
        for run_id in affected:
            if statuses.get(run_id) != "cancelled":
                log_event(logger, logging.WARNING, "workspace_stop_unconfirmed", "web_api",
                          run_id=str(run_id), grant_id=str(grant_id))
        return {"affected_runs": [{"run_id": str(run_id),
                "stop_state": "stopped" if statuses.get(run_id) == "cancelled" else "stopping"}
                for run_id in affected]}

    @app.delete("/api/v1/conversations/{conversation_id}/attachments/{file_id}")
    def revoke_conversation_attachment(conversation_id: UUID, file_id: UUID,
                                       request: Request,
                                       context: AuthContext = Depends(csrf_guard)):
        affected = request.app.state.resource_policy.revoke_conversation_file(
            context.account_id, conversation_id, file_id, request.app.state.commands
        )
        return {"affected_runs": [{"run_id": str(run_id), "stop_state": "stopping"}
                                  for run_id in affected]}

    @app.get("/api/v1/runs/{run_id}/resources")
    def run_resources(run_id: UUID, request: Request,
                      context: AuthContext = Depends(auth_context),
                      after: str | None = Query(default=None, max_length=255),
                      limit: int = Query(default=50, ge=1, le=100)):
        return request.app.state.resource_policy.candidates(
            context.account_id, run_id, after, limit
        )

    @app.post("/api/v1/workspace/directories", status_code=201)
    def create_workspace_directory(
        payload: CreateWorkspaceDirectoryRequest, request: Request,
        context: AuthContext = Depends(csrf_guard),
    ):
        node_id = workspace_mutation(lambda: request.app.state.workspace.create_directory(
            context.account_id, payload.parent_id, payload.name
        ))
        return {"node_id": str(node_id)}

    @app.post("/api/v1/workspace/files", status_code=201)
    def save_workspace_file(
        payload: SaveWorkspaceFileRequest, request: Request,
        context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key),
    ):
        node_id = workspace_mutation(lambda: request.app.state.workspace.save_file(
            context.account_id, payload.parent_id, payload.file_id, payload.name, key
        ))
        return {"node_id": str(node_id)}

    @app.get("/api/v1/workspace/nodes/{node_id}/versions")
    def workspace_versions(node_id: UUID, request: Request,
                           context: AuthContext = Depends(auth_context)):
        return request.app.state.workspace.versions(context.account_id, node_id)

    @app.get("/api/v1/runs/{run_id}/published-files")
    def run_published_files(run_id: UUID, request: Request,
                            context: AuthContext = Depends(auth_context)):
        with UnitOfWork(request.app.state.api_pool) as uow:
            if uow.execute("SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s",
                           (context.account_id, run_id)).fetchone() is None:
                raise ResourceNotFound()
            rows = uow.execute(
                "SELECT rf.file_id,sf.display_name,sf.sha256 FROM run_files rf "
                "JOIN stored_files sf ON sf.account_id=rf.account_id AND sf.file_id=rf.file_id "
                "WHERE rf.account_id=%s AND rf.run_id=%s AND rf.direction='output' "
                "AND sf.status='ready' AND sf.source_run_id=%s ORDER BY rf.file_id",
                (context.account_id, run_id, run_id),
            ).fetchall()
        return {"files": [{"file_id": str(r["file_id"]), "name": r["display_name"],
                           "sha256": r["sha256"]} for r in rows]}

    @app.post("/api/v1/workspace/nodes/{node_id}/upgrade")
    def upgrade_workspace_file(node_id: UUID, request: Request,
                               context: AuthContext = Depends(csrf_guard)):
        return workspace_mutation(lambda: request.app.state.workspace.upgrade_file(
            context.account_id, node_id))

    @app.post("/api/v1/workspace/nodes/{node_id}/versions", status_code=201)
    def update_workspace_file(node_id: UUID, payload: UpdateWorkspaceFileRequest,
                              request: Request, context: AuthContext = Depends(csrf_guard),
                              key: str = Depends(idempotency_key)):
        try:
            return workspace_mutation(lambda: request.app.state.workspace.update_file(
                context.account_id, node_id, payload.run_id, payload.file_id,
                payload.expected_revision, payload.expected_sha256, key))
        except WorkspaceVersionConflict as exc:
            log_event(logger, logging.WARNING, "workspace_cas_conflict", "web_api",
                      node_id=str(node_id), run_id=str(payload.run_id),
                      file_id=str(payload.file_id))
            return _error(request, 409, "workspace_version_conflict",
                          "Workspace 版本已更新，已发布输出可另存。", details={
                              "current": exc.current,
                              "published_file_id": str(payload.file_id)})

    @app.get("/api/v1/workspace/nodes/{node_id}/impact")
    def workspace_node_impact(node_id: UUID, request: Request,
                              context: AuthContext = Depends(auth_context)):
        return request.app.state.workspace.preview_change(context.account_id, node_id)

    @app.patch("/api/v1/workspace/nodes/{node_id}")
    def move_workspace_node(
        node_id: UUID, payload: MoveWorkspaceNodeRequest, request: Request,
        context: AuthContext = Depends(csrf_guard),
    ):
        workspace_mutation(lambda: request.app.state.workspace.move(
            context.account_id, node_id, payload.parent_id, payload.name,
            payload.preview_token
        ))
        return {"node_id": str(node_id)}

    @app.delete("/api/v1/workspace/nodes/{node_id}", status_code=204)
    def remove_workspace_node(
        node_id: UUID, request: Request,
        context: AuthContext = Depends(csrf_guard),
    ):
        preview_token = request.headers.get("X-Workspace-Preview")
        if not preview_token:
            raise HTTPException(status_code=422, detail="Workspace impact preview is required")
        workspace_mutation(lambda: request.app.state.workspace.remove(
            context.account_id, node_id, preview_token
        ))
        return Response(status_code=204)

    @app.delete("/api/v1/files/{file_id}", status_code=204)
    def delete_file(
        file_id: UUID,
        request: Request,
        context: AuthContext = Depends(csrf_guard),
        files: FileService = Depends(file_service),
    ):
        files.delete(context.account_id, file_id)
        return Response(status_code=204)

    @app.get("/api/v1/files/{file_id}/lineage")
    def get_file_lineage(
        file_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
        files: FileService = Depends(file_service),
    ):
        return {"files": files.lineage(context.account_id, file_id)}

    @app.get("/api/v1/runs/{run_id}/file-action-approvals")
    def list_file_action_approvals(
        run_id: UUID, request: Request, context: AuthContext = Depends(auth_context),
    ):
        return {"approvals": request.app.state.file_approvals.list_for_run(
            context.account_id, run_id
        )}

    @app.post("/api/v1/file-action-approvals/{approval_id}/approve")
    def approve_file_action(
        approval_id: UUID, payload: EmptyRequest, request: Request,
        context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key),
    ):
        del payload
        result = request.app.state.file_approvals.decide(
            context.account_id, approval_id, "approved", key
        )
        response = JSONResponse(status_code=result.response_status, content=result.body)
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.post("/api/v1/file-action-approvals/{approval_id}/reject")
    def reject_file_action(
        approval_id: UUID, payload: EmptyRequest, request: Request,
        context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key),
    ):
        del payload
        result = request.app.state.file_approvals.decide(
            context.account_id, approval_id, "rejected", key
        )
        response = JSONResponse(status_code=result.response_status, content=result.body)
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.get("/api/v1/files/{file_id}/content")
    def download_file(
        file_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
        files: FileService = Depends(file_service),
    ):
        metadata, stream = files.download(context.account_id, file_id)

        def body():
            try:
                while chunk := stream.read(64 * 1024):
                    yield chunk
            finally:
                stream.close()

        encoded_name = quote(str(metadata["file_name"]), safe="")
        headers = {
            "Content-Disposition": (
                f"attachment; filename=download.txt; filename*=UTF-8''{encoded_name}"
            )
        }
        return StreamingResponse(
            body(), media_type="application/octet-stream", headers=headers
        )

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
        if payload.file_ids and not settings.web_file_upload_enabled:
            return _error(
                request, 409, "file_capability_disabled", "文件能力尚未启用。"
            )
        if len(payload.file_ids) > settings.file_max_count_per_message:
            return _error(
                request, 422, "file_processing_limit", "附件数量超过限制。"
            )
        try:
            content = _normalize_content(payload.content)
        except EmptyMessage:
            return _error(request, 422, "empty_message", "消息不能为空。")
        except MessageTooLarge:
            return _error(request, 413, "message_too_large", "消息过长。")
        result: CommandResult = request.app.state.commands.send_message(
            context.account_id,
            conversation_id,
            key,
            content,
            payload.agent_strategy,
            tuple(payload.file_ids),
        )
        run = result.body["run"]
        log_event(
            logger, logging.INFO, "web_message_accepted", "web_api",
            request_id=request.state.request_id, run_id=run["run_id"],
            conversation_id=str(conversation_id), status="success",
        )
        body = command_body(result, "user_message", "assistant_message", "run", events=True)
        response = JSONResponse(status_code=result.response_status, content=body)
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
        return request.app.state.queries.get_run(context.account_id, run_id)

    @app.get("/api/v1/runs/{run_id}/trace")
    def get_run_trace(
        run_id: UUID,
        request: Request,
        context: AuthContext = Depends(auth_context),
    ):
        tree = request.app.state.trace_repository.get_trace_tree(
            context.account_id, run_id
        )
        if tree is None:
            raise ResourceNotFound()
        return trace_tree_dto(tree)

    @app.get("/api/v1/runs/{run_id}/model-inputs")
    def list_run_model_inputs(
        run_id: UUID, request: Request, context: AuthContext = Depends(auth_context),
    ):
        return request.app.state.model_observability.list_run_model_snapshots(
            context.account_id, run_id
        )

    @app.get("/api/v1/model-inputs/{snapshot_id}")
    def get_model_input(
        snapshot_id: UUID, request: Request, context: AuthContext = Depends(auth_context),
    ):
        return request.app.state.model_observability.get_model_snapshot(
            context.account_id, snapshot_id
        )

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
            if str(exc) == "artifact_research_version_unsupported":
                return _error(
                    request,
                    409,
                    "artifact_research_version_unsupported",
                    "Research Artifact 当前只能由 Research Workflow 生成版本。",
                )
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
            content=command_body(result, "run", "assistant_message"),
        )
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return response

    @app.post("/api/v1/runs/{run_id}/retry")
    def retry_run(run_id: UUID, payload: EmptyRequest, request: Request, context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key)):
        result: CommandResult = request.app.state.commands.retry_run(context.account_id, run_id, key)
        body = command_body(result, "source_run_id", "assistant_message", "run", events=True)
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
