from __future__ import annotations

import hmac
import html
import logging
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from account.identity_binding_service import ChallengeNotFound, mask_qq_subject
from account.invite_service import InvalidRegistrationInvite
from account.registration_service import InvalidPassword, InvalidUsername, UsernameAlreadyExists
from web_api.auth import AuthContext
from web_api.dependencies import (
    CsrfInvalid,
    auth_context,
    csrf_guard,
    request_origin,
    session_cookie_name,
)
from web_api.models import LoginRequest, RegisterRequest
from web_api.responses import error_response

logger = logging.getLogger("HpAgent.WebApi")

router = APIRouter()


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request):
    subject = request.app.state.credentials.verify(payload.username, payload.password)
    context = request.app.state.auth.login(subject) if subject else None
    if not context:
        return error_response(request, 401, "unauthenticated", "登录凭证无效。", retryable=True)
    return_to = payload.return_to
    if not return_to.startswith("/") or return_to.startswith("//") or urlsplit(return_to).scheme:
        return_to = "/"
    response = RedirectResponse(return_to, status_code=303)
    response.set_cookie(
        session_cookie_name(request),
        context.raw_session_token,
        path="/",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/auth/register")
def register(payload: RegisterRequest, request: Request):
    try:
        result = request.app.state.registration.register(
            payload.username, payload.password, payload.invite_code
        )
    except InvalidRegistrationInvite:
        return error_response(request, 403, "registration_invite_invalid", "邀请码无效或不可用。")
    except UsernameAlreadyExists:
        return error_response(request, 409, "username_already_exists", "用户名已存在。")
    except InvalidUsername:
        return error_response(request, 422, "invalid_username", "用户名无效。")
    except InvalidPassword:
        return error_response(
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
        session_cookie_name(request),
        context.raw_session_token,
        path="/",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/auth/login", response_class=HTMLResponse)
def login_entry(return_to: str = "/") -> str:
    safe_return = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/"
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>HpAgent 登录</title>"
        "</head><body><main><h1>HpAgent 登录</h1><p>请通过已配置的同源登录客户端提交凭证。"
        f"</p><p data-return-to='{html.escape(safe_return, quote=True)}'>登录后返回当前页面。</p></main></body></html>"
    )


@router.get("/api/v1/me")
def me(request: Request, context: AuthContext = Depends(auth_context)) -> dict[str, Any]:
    identities = request.app.state.identity_bindings.get_identity_summary(context.account_id)
    return {
        "account": {
            "account_id": str(context.account_id),
            "status": "active",
            "created_at": context.account_created_at,
        },
        "session": {"expires_at": context.expires_at, "idle_expires_at": context.idle_expires_at},
        "csrf_token": context.csrf_token,
        "identities": identities,
        "capabilities": {
            "qq_long_term_memory_shared": True,
            "qq_self_service_binding": True,
            "durable_agent": True,
            "file_upload": request.app.state.settings.web_file_upload_enabled,
            "file_transform": request.app.state.settings.web_file_transform_enabled,
            "agent_strategies": ["react", "plan_and_execute"],
        },
    }


@router.post("/api/v1/identity-bindings/qq/challenges", status_code=201)
def create_qq_binding_challenge(
    request: Request, context: AuthContext = Depends(csrf_guard)
) -> dict[str, Any]:
    challenge = request.app.state.identity_bindings.create_qq_challenge(context.account_id)
    return {
        "challenge_id": str(challenge.challenge_id),
        "code": challenge.code,
        "expires_at": challenge.expires_at,
        "instruction": f"请使用需要绑定的 QQ 向 HpAgent 发送：绑定 {challenge.code}",
    }


@router.get("/api/v1/identity-bindings/qq/challenges/{challenge_id}")
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
        return error_response(request, 404, "resource_not_found", "绑定验证不存在。")
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


@router.post("/api/v1/auth/logout", status_code=204)
def logout(request: Request):
    raw = request.cookies.get(session_cookie_name(request))
    context = request.app.state.auth.authenticate(raw) if raw else None
    if context:
        submitted = request.headers.get("x-csrf-token", "")
        if request_origin(
            request
        ) != request.app.state.settings.public_origin or not hmac.compare_digest(
            submitted, context.csrf_token
        ):
            raise CsrfInvalid()
        request.app.state.auth.revoke(context)
    response = Response(status_code=204)
    response.delete_cookie(
        session_cookie_name(request),
        path="/",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
