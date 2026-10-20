from __future__ import annotations

from fastapi import FastAPI

from .auth import CredentialAdapter
from .config import WebApiSettings
from .error_handlers import register_exception_handlers
from .lifespan import create_lifespan
from .middleware import BodyLimitMiddleware, CommonHeadersMiddleware, ProtocolMiddleware
from .routes import artifacts, auth, conversations, files, health, runs, works


def create_app(
    settings: WebApiSettings | None = None,
    credential_adapter: CredentialAdapter | None = None,
) -> FastAPI:
    settings = settings or WebApiSettings.from_env()
    app = FastAPI(
        title="HpAgent Web API",
        version="0.3",
        lifespan=create_lifespan(settings, credential_adapter),
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(BodyLimitMiddleware, upload_limit=settings.file_max_bytes)
    app.add_middleware(ProtocolMiddleware)
    app.add_middleware(CommonHeadersMiddleware)
    register_exception_handlers(app)
    for router in (
        health.router,
        auth.router,
        conversations.router,
        works.router,
        files.router,
        runs.router,
        artifacts.router,
    ):
        app.include_router(router)
    return app


app = create_app
