"""QQ surface and shared Agent execution composition."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from account.postgres_account_service import PostgresAccountService
from account.validation import validate_unified_account_backend
from actions.runtime import ActionRuntime
from application.memory_reflection import MemoryReflectionService
from application.metrics import MetricsSnapshotService
from application.reply import ReplyService
from brain.engine import BrainEngine
from storage.file_store import LocalFileStore


@dataclass(frozen=True)
class QQRuntimeServices:
    account_service: Any
    reply_service: ReplyService
    brain_engine: BrainEngine
    action_runtime: ActionRuntime
    memory_reflection: MemoryReflectionService
    metrics: MetricsSnapshotService


def build_qq_runtime(
    *,
    config: Any,
    worker_database_url: str | None,
    redis_cache: Any,
    hindsight_client: Any,
    context_builder: Any,
    channel_router: Any,
    group_context: Any,
    sandbox_manager: Any,
    resource_pool: Any,
    prompt_loader: Any,
    file_store: LocalFileStore,
) -> QQRuntimeServices:
    """Build protocol delivery and shared capabilities; no QQ Host, loop or SessionStore."""
    validate_unified_account_backend(worker_database_url)
    account_service = PostgresAccountService(worker_database_url)

    memory = HindsightMaintenance(hindsight_client)
    brain = BrainEngine(resource_pool=resource_pool, prompts=prompt_loader)
    reply = ReplyService(
        channel_router=channel_router,
        group_context=group_context,
        prompts=prompt_loader,
    )
    actions = ActionRuntime(
        sandbox_manager=sandbox_manager,
        resource_pool=resource_pool,
        prompts=prompt_loader,
        tool_rag_top_k=config.models.tool_rag.top_k,
        tool_result_summary_enabled=config.agent.tool_result_summary_enabled,
        tool_result_summary_threshold=config.agent.tool_result_summary_threshold,
        tool_result_summary_max_chars=config.agent.tool_result_summary_max_chars,
    )
    return QQRuntimeServices(
        account_service=account_service,
        reply_service=reply,
        brain_engine=brain,
        action_runtime=actions,
        memory_reflection=MemoryReflectionService(memory),
        metrics=MetricsSnapshotService(memory),
    )


class HindsightMaintenance:
    """Scheduled long-term capabilities; no short-term Session authority."""
    def __init__(self, hindsight):
        self.hindsight = hindsight

    async def reflect(self, account_id: str) -> int:
        return await self.hindsight.reflect(account_id) if self.hindsight else 0

    async def get_metrics(self) -> dict:
        return self.hindsight.get_metrics() if self.hindsight else {}
