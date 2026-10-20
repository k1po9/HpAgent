from __future__ import annotations

import hmac
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, Request

from web_api.auth import AuthContext
from web_domain.errors import ResourceNotFound
from web_domain.file_services import FileService


def auth_context(request: Request) -> AuthContext:
    raw = request.cookies.get(session_cookie_name(request))
    context = request.app.state.auth.authenticate(raw) if raw else None
    if not context:
        raise Unauthenticated()
    return cast(AuthContext, context)


def csrf_guard(request: Request, context: AuthContext = Depends(auth_context)) -> AuthContext:
    source = request_origin(request)
    submitted = request.headers.get("x-csrf-token", "")
    if source != request.app.state.settings.public_origin or not hmac.compare_digest(
        submitted, context.csrf_token
    ):
        raise CsrfInvalid()
    return context


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


def request_origin(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        return origin
    referer = request.headers.get("referer", "")
    if not referer:
        return ""
    parsed = urlsplit(referer)
    return f"{parsed.scheme}://{parsed.netloc}"


def session_cookie_name(request: Request) -> str:
    # __Host- cookies require Secure; local HTTP uses the plain cookie name.
    return (
        "__Host-hpagent_session" if request.app.state.settings.cookie_secure else "hpagent_session"
    )


class Unauthenticated(Exception):
    pass


class CsrfInvalid(Exception):
    pass


class InvalidIdempotencyKey(Exception):
    pass
