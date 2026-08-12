"""QQ surface and shared Agent execution composition."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from account.postgres_account_service import PostgresAccountService
from account.validation import validate_unified_account_backend
from actions.runtime import ActionRuntime
from agent_execution.brain_action_loop import DefaultBrainActionLoop
from agent_execution.facade import AgentExecutionFacade
from agent_execution.qq_host import (
    QQExecutionHost,
    QQLegacyExecutionControl,
    QQLegacyRequestLoader,
    ReplyServiceQQEventSinkFactory,
    ReplyServiceQQSink,
    TurnMemoryQQAuditSinkFactory,
    TurnMemoryQQRetentionSink,
)
from application.memory import TurnMemoryService
from application.memory_reflection import MemoryReflectionService
from application.metrics import MetricsSnapshotService
from application.reply import ReplyService
from application.session_archive import SessionArchiveService
from brain.engine import BrainEngine
from session.store import SessionStore
from storage.file_store import LocalFileStore


@dataclass(frozen=True)
class QQRuntimeServices:
    account_service: Any
    reply_service: ReplyService
    brain_engine: BrainEngine
    action_runtime: ActionRuntime
    execution_host: QQExecutionHost
    session_archive: SessionArchiveService
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
    """Build the QQ surface around the same Facade used by Web."""
    validate_unified_account_backend(worker_database_url)
    account_service = PostgresAccountService(worker_database_url)

    backup_store = None
    if config.session.backup_dir:
        backup_store = LocalFileStore(root=Path(config.session.backup_dir))
    session_store = SessionStore(
        redis_cache=redis_cache,
        hindsight_client=hindsight_client,
        file_store=backup_store,
        wal_enabled=config.agent.wal_enabled,
        checkpoint_enabled=config.agent.checkpoint_interval > 0,
    )
    channel_overrides = {
        name: {
            "max_tokens": item.max_tokens,
            "timeout": item.timeout,
            "stream": item.stream,
        }
        for name, item in config.models.channel_overrides.items()
    }
    memory = TurnMemoryService(session_store=session_store)
    brain = BrainEngine(resource_pool=resource_pool, prompts=prompt_loader)
    reply = ReplyService(
        channel_router=channel_router,
        group_context=group_context,
        prompts=prompt_loader,
    )
    actions = ActionRuntime(
        sandbox_manager=sandbox_manager,
        session_store=session_store,
        resource_pool=resource_pool,
        prompts=prompt_loader,
        tool_rag_top_k=config.models.tool_rag.top_k,
        tool_result_summary_enabled=config.agent.tool_result_summary_enabled,
        tool_result_summary_threshold=config.agent.tool_result_summary_threshold,
        tool_result_summary_max_chars=config.agent.tool_result_summary_max_chars,
    )
    host = QQExecutionHost(
        QQLegacyRequestLoader(memory, context_builder, group_context, channel_overrides),
        AgentExecutionFacade(
            DefaultBrainActionLoop(brain, actions, max_tool_turns=config.agent.max_tool_turns)
        ),
        ReplyServiceQQEventSinkFactory(reply),
        ReplyServiceQQSink(reply),
        QQLegacyExecutionControl(),
        allow_legacy_missing_message_id=True,
        audit=TurnMemoryQQAuditSinkFactory(memory),
        retention=TurnMemoryQQRetentionSink(memory),
    )
    return QQRuntimeServices(
        account_service=account_service,
        reply_service=reply,
        brain_engine=brain,
        action_runtime=actions,
        execution_host=host,
        session_archive=SessionArchiveService(
            memory_service=memory,
            action_runtime=actions,
            resource_pool=resource_pool,
            prompts=prompt_loader,
            file_store=file_store,
            group_context=group_context,
        ),
        memory_reflection=MemoryReflectionService(memory),
        metrics=MetricsSnapshotService(memory),
    )
