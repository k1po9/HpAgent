from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from account.credentials import PostgresPasswordCredentialAdapter
from account.identity_binding_service import IdentityBindingService
from account.registration_service import RegistrationService
from common.agent_policy import load_agent_policy
from common.runtime_config import CapacityConfig
from conversation_domain.commands import CommandService
from file_domain.approvals import FileActionApprovalService
from persistence.migrate import verify_schema
from storage.tenant_file_store import TenantFileStore
from tracing.repository import PostgresTraceRepository
from web_api.auth import AuthService, CredentialAdapter
from web_api.config import WebApiSettings
from web_api.fake_executor import FakeArtifactExecutor, FakeRunExecutor
from web_api.model_observability_queries import ModelObservabilityQueries
from web_api.queries import QueryService
from web_api.security import CursorCodec
from web_api.sse import SSEGateway
from web_api.terminal_publisher import TerminalEventPublisher
from web_artifacts.services import ArtifactService
from web_domain.file_services import FileService
from work_domain.commands import WorkCommandService
from workspace.catalog import WorkspaceCatalog
from workspace.resources import ResourcePolicy


def create_lifespan(
    settings: WebApiSettings,
    credential_adapter: CredentialAdapter | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        agent_policy = load_agent_policy()
        CapacityConfig.from_environment()
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
            try:
                verify_schema(api_pool)
            except Exception:
                import logging

                logging.getLogger("HpAgent.WebAPI").exception("API schema gate failed")
                raise
            from application.memory_governance import MemoryGovernanceService
            from memory.hindsight_client import HindsightClient
            memory_reader = HindsightClient(settings.hindsight_url, api_key=settings.hindsight_api_key) if settings.hindsight_url else None
            app.state.memory_governance = MemoryGovernanceService(api_pool, memory_reader)
            app.state.api_pool = api_pool
            app.state.auth = AuthService(api_pool, settings)
            app.state.credentials = credential_adapter or PostgresPasswordCredentialAdapter(
                api_pool
            )
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
                agent_policy=agent_policy,
            )
            app.state.works = WorkCommandService(api_pool, budget_mode=settings.run_budget_mode, agent_policy=agent_policy)
            app.state.file_approvals = FileActionApprovalService(api_pool)
            app.state.workspace = WorkspaceCatalog(api_pool, app.state.commands)
            app.state.resource_policy = ResourcePolicy(api_pool)
            if settings.web_file_upload_enabled:
                file_root = Path(settings.file_store_root).resolve()
                application_root = Path.cwd().resolve()
                if file_root == application_root or file_root.is_relative_to(application_root):
                    raise RuntimeError(
                        "FILE_STORE_ROOT must be outside the application/Git workspace"
                    )
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

                redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
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

    return lifespan
