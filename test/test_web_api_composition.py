"""HTTP composition must keep per-app security settings and resource ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from web_api.app import create_app
from web_api.auth import AuthContext
from web_api.config import WebApiSettings


def settings(**overrides):
    return WebApiSettings(
        **{
            "database_url": "postgresql://unused",
            "public_origin": "https://secure.test",
            "cursor_signing_keys": {"v1": b"x" * 32},
            "active_cursor_key_id": "v1",
            "session_token_pepper": b"y" * 32,
            "csrf_signing_key": b"z" * 32,
            "environment": "test",
            **overrides,
        }
    )


def authenticated_app(config):
    context = AuthContext(
        account_id=uuid4(),
        identity_binding_id=uuid4(),
        web_auth_session_id=uuid4(),
        account_created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        idle_expires_at=datetime.now(UTC),
        csrf_token=str(uuid4()),
        raw_session_token=str(uuid4()),
    )
    app = create_app(config)
    app.state.auth = SimpleNamespace(
        authenticate=lambda raw: context if raw == context.raw_session_token else None,
        login=lambda subject: context,
        revoke=Mock(),
    )
    app.state.credentials = SimpleNamespace(verify=lambda username, password: username)
    app.state.identity_bindings = SimpleNamespace(get_identity_summary=lambda account_id: {})
    return app, context


async def test_shared_routers_keep_each_apps_cookie_origin_and_capabilities():
    secure, secure_context = authenticated_app(settings(web_file_upload_enabled=True))
    local, local_context = authenticated_app(
        settings(
            public_origin="http://local.test",
            cookie_secure=False,
        )
    )
    for app, context, origin, cookie_name, file_upload in (
        (secure, secure_context, "https://secure.test", "__Host-hpagent_session", True),
        (local, local_context, "http://local.test", "hpagent_session", False),
        (secure, secure_context, "https://secure.test", "__Host-hpagent_session", True),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=origin,
        ) as client:
            logged_in = await client.post(
                "/auth/login",
                json={
                    "username": "alice",
                    "password": "password",
                    "return_to": "//outside.test",
                },
            )
            assert logged_in.status_code == 303
            assert logged_in.headers["location"] == "/"
            cookie = logged_in.headers["set-cookie"]
            assert cookie.startswith(f"{cookie_name}=")
            assert ("; Secure" in cookie) == app.state.settings.cookie_secure
            me = await client.get("/api/v1/me")
            assert me.json()["account"]["account_id"] == str(context.account_id)
            assert me.json()["capabilities"]["file_upload"] == file_upload
            rejected = await client.post(
                "/api/v1/auth/logout",
                headers={
                    "Origin": "https://other.test",
                    "X-CSRF-Token": context.csrf_token,
                },
            )
            assert rejected.status_code == 403
            assert rejected.json()["error"]["code"] == "csrf_invalid"
            assert rejected.headers["cache-control"] == "no-store"
            app.state.auth.revoke.assert_not_called()
            accepted = await client.post(
                "/api/v1/auth/logout",
                headers={
                    "Origin": origin,
                    "X-CSRF-Token": context.csrf_token,
                },
            )
            assert accepted.status_code == 204
            assert accepted.headers["set-cookie"].startswith(f'{cookie_name}=""')
            app.state.auth.revoke.assert_called_with(context)
            app.state.auth.revoke.reset_mock()


@pytest.mark.asyncio
async def test_partial_startup_stops_publisher_and_redis_before_api_pool(monkeypatch):
    import redis.asyncio as aioredis

    from web_api import lifespan

    events = []
    api_pool = SimpleNamespace(wait=Mock(), close=lambda: events.append("api_closed"))
    worker_pool = SimpleNamespace(
        wait=Mock(side_effect=RuntimeError("worker pool unavailable")),
        close=lambda: events.append("worker_closed"),
    )

    async def redis_close():
        events.append("redis_closed")

    async def publisher_stop():
        events.append("publisher_stopped")

    redis = SimpleNamespace(aclose=AsyncMock(side_effect=redis_close))
    publisher = SimpleNamespace(start=Mock(), stop=AsyncMock(side_effect=publisher_stop))
    monkeypatch.setattr(lifespan, "ConnectionPool", Mock(side_effect=[api_pool, worker_pool]))
    monkeypatch.setattr(lifespan, "verify_schema", Mock())
    monkeypatch.setattr(aioredis, "from_url", Mock(return_value=redis))
    monkeypatch.setattr(lifespan, "TerminalEventPublisher", Mock(return_value=publisher))
    app = create_app(
        settings(
            worker_database_url="postgresql://unused-worker",
            fake_executor_enabled=True,
            redis_url="redis://unused",
        )
    )
    with pytest.raises(RuntimeError, match="worker pool unavailable"):
        async with app.router.lifespan_context(app):
            pytest.fail("startup must not yield an unready application")
    publisher.start.assert_called_once_with()
    assert events == ["worker_closed", "publisher_stopped", "redis_closed", "api_closed"]
