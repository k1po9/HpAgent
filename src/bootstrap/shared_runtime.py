"""Composition of canonical capabilities shared by every Agent surface."""
from __future__ import annotations

from dataclasses import dataclass

from account.postgres_account_service import PostgresAccountService
from account.validation import validate_unified_account_backend
from actions.runtime import ActionRuntime
from application.context_builder import HarnessContextBuilder
from application.memory_reflection import MemoryReflectionService
from application.metrics import MetricsSnapshotService
from application.prompts import PromptLoader
from brain.engine import BrainEngine
from memory.maintenance import HindsightMaintenance
from orchestration.config import AppConfig
from resources.resource_pool import ResourcePool
from sandbox.sandbox_manager import SandboxManager


@dataclass(frozen=True)
class SharedAgentServices:
    """One process-wide set of capabilities consumed by durable Agent work."""

    account_service: PostgresAccountService
    prompt_loader: PromptLoader
    context_builder: HarnessContextBuilder
    brain_engine: BrainEngine
    action_runtime: ActionRuntime
    hindsight_client: object | None
    memory_reflection: MemoryReflectionService
    metrics: MetricsSnapshotService


def build_shared_agent_services(
    *,
    config: AppConfig,
    worker_database_url: str | None,
    sandbox_manager: SandboxManager,
    resource_pool: ResourcePool,
    hindsight_client: object | None,
    prompt_loader: PromptLoader,
) -> SharedAgentServices:
    """Build the single shared Agent capability set for the worker process."""
    validate_unified_account_backend(worker_database_url)
    assert worker_database_url is not None
    memory = HindsightMaintenance(hindsight_client)
    return SharedAgentServices(
        account_service=PostgresAccountService(worker_database_url),
        prompt_loader=prompt_loader,
        context_builder=HarnessContextBuilder(
            prompt_loader=prompt_loader,
            enable_tool_guidance=True,
        ),
        brain_engine=BrainEngine(resource_pool=resource_pool, prompts=prompt_loader),
        action_runtime=ActionRuntime(
            sandbox_manager=sandbox_manager,
            resource_pool=resource_pool,
            prompts=prompt_loader,
            tool_rag_top_k=config.models.tool_rag.top_k,
            tool_result_summary_enabled=config.agent.tool_result_summary_enabled,
            tool_result_summary_threshold=config.agent.tool_result_summary_threshold,
            tool_result_summary_max_chars=config.agent.tool_result_summary_max_chars,
        ),
        hindsight_client=hindsight_client,
        memory_reflection=MemoryReflectionService(memory),
        metrics=MetricsSnapshotService(memory),
    )
