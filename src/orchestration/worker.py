"""
Orchestration Worker —— Temporal Worker 启动与依赖组装。

启动序列:
  1. AppConfig.from_yaml() → 加载全量结构化配置
  2. init_dependencies()    → 按 config 组装所有依赖
  3. 构造 instance-owned Activities
  4. 连接 Temporal → 启动 Worker → 渠道监听

架构:
  Web / QQ → PG Conversation commands + Outbox → AgentLifecycleWorkflow → AgentRunWorkflow → Strategy Workflow
      ↓
  BrainEngine / ActionRuntime / Surface adapters
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Dict

from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from account.entitlement_service import EntitlementService
from account.identity_binding_service import IdentityBindingService
from application.conversation import ConversationService
from application.identity_commands import IdentityCommandService
from application.ingress import MessageIngressService
from application.prompts import PromptLoader
from application.scheduler import TaskScheduler
from bootstrap.qq import QQSurfaceServices, build_qq_surface_services
from bootstrap.shared_runtime import SharedAgentServices, build_shared_agent_services
from channels.napcat import NapCatChannel
from channels.official_qq import OfficialQQChannel
from channels.router import ChannelRouter
from common.types import ChannelType, UnifiedMessage
from memory.activities import ScheduledMemoryActivities
from memory.workflows import MetricsReportWorkflow, ReflectWorkflow
from orchestration.config import AppConfig, SandboxConfig
from resources.credentials import CredentialManager, ModelEndpoint
from resources.model_budget_coordinator import ModelBudgetCoordinator
from resources.model_input_snapshot import SnapshotRepository
from resources.resource_pool import ResourcePool
from sandbox.git_repo import GitRepoManager
from sandbox.nsjail import NsjailConfig
from sandbox.sandbox_manager import SandboxManager
from storage.tenant_file_store import TenantFileStore
from workspace.file_capability_config import FileCapabilityConfig
from workspace.file_scope import RunFileWorkspace
from workspace.isolation import WorkspaceIsolationRuntime

logger = logging.getLogger("HpAgent.OrchestrationWorker")

_DEVELOPMENT_QQ_BINDING_CODE_PEPPER = b"development-qq-binding-code-pepper"


def load_worker_qq_binding_code_pepper() -> bytes:
    """Load the shared challenge pepper; production must configure it safely."""
    environment = os.getenv("HPAGENT_ENV", "development").strip().casefold()
    configured = os.getenv("QQ_BINDING_CODE_PEPPER")
    if environment == "production" and not configured:
        raise RuntimeError(
            "QQ_BINDING_CODE_PEPPER must be explicitly set for the Worker in production"
        )
    pepper = (
        configured.encode("utf-8")
        if configured
        else _DEVELOPMENT_QQ_BINDING_CODE_PEPPER
    )
    if environment == "production" and len(pepper) < 32:
        raise RuntimeError(
            "QQ_BINDING_CODE_PEPPER must contain at least 32 bytes in production"
        )
    return pepper


@dataclasses.dataclass
class DurableRuntimeComposition:
    """Canonical durable workers, dispatchers and reconcilers.

    单一进程部署（QQ + Web 同进程，AE-021 的 ``single_process_account_lock``）
    与独立 Web Worker 入口都通过同一个组合函数组装，保证二者使用同一个
    ``SessionResourceRecoveryService`` 与共享 ``AccountLockRegistry``。
    """
    workers: object  # WebTemporalWorkers
    dispatcher: object  # WebOutboxDispatcher
    reconciler: object  # WebRunReconciler
    memory_retention: object = None  # MemoryRetentionService | None (Phase F)
    artifact_dispatcher: object = None


def compose_durable_runtime(
    client, config: AppConfig, deps: WorkerDependencies
) -> DurableRuntimeComposition:
    """组装 Web lifecycle/agent Worker、Outbox Dispatcher 与 Reconciler。

    Web real Agent 或任一 QQ channel 启用时组装唯一 canonical runtime（C-07 门禁）。
    该方法不启动任何 Worker/后台任务，也不注册 QQ/scheduler/channel/schedule；
    调用方决定进程边界。Web 真实执行会经 ``SessionResourceRecoveryService``
    获取共享 Account 执行锁、恢复 Run 绑定 Session 的 workspace 并创建 Sandbox。
    """
    worker_database_url = os.getenv("WORKER_DATABASE_URL")

    from agent_activities.runtime import DurableAgentActivities
    from agent_activities.segments import SegmentActivities
    from agent_activities.store import AgentDataStore
    from application.chat_execution import (
        PostgresWebRequestLoader,
    )
    from application.context_assembly import ContextAssemblyService
    from conversation_domain.execution_bindings import ChatExecutionBindings
    from file_domain.approvals import FileActionApprovalService
    from file_runtime import ResearchMarkdownPublisher
    from orchestration.artifact_activities import ArtifactActivities
    from orchestration.artifact_dispatcher import (
        ArtifactOutboxDispatcher,
        TemporalArtifactClient,
    )
    from orchestration.run_lifecycle_activities import RunLifecycleActivities
    from orchestration.web_dispatcher import (
        TemporalClientAdapter,
        TemporalOutboxDispatcher,
        WebOutboxDispatcher,
    )
    from orchestration.web_reconcile_adapters import LifecycleReconcileStore
    from orchestration.web_reconciler import (
        TemporalInspectorAdapter,
        WebRunReconciler,
    )
    from orchestration.web_workers import (
        build_web_temporal_workers,
        validate_web_worker_startup,
    )
    from research_activities import ResearchActivities
    from research_adapters import (
        CompositeSourceDiscoveryProvider,
        GitHubDiscoveryProvider,
        PlaywrightBrowserFetchProvider,
        ResourcePoolResearchSynthesizer,
        RSSDiscoveryProvider,
        SearXNGDiscoveryProvider,
        StaticWebContentProvider,
        W3libSourceCanonicalizer,
    )
    from resources.run_budget import RunBudgetService
    from tracing import (
        PostgresTraceRepository,
        TraceLifecycleObserver,
        TracingWebEventSinkFactory,
    )
    from web_artifacts.build import ArtifactBuildService
    from web_artifacts.generator import WebArtifactGenerator
    from web_artifacts.outbox import ArtifactOutboxService
    from web_domain.lifecycle import WebRunLifecycleService
    from web_domain.outbox import OutboxService
    from web_domain.run_events import RedisWebRunEventSinkFactory
    from web_domain.workflow_execution import PostgresWorkflowExecutionStore
    from workspace.isolation import SessionResourceRecoveryService

    validate_web_worker_startup(config.temporal, worker_database_url)
    assert worker_database_url is not None
    from persistence.migrate import verify_schema
    verify_schema(worker_database_url)
    infrastructure = deps.infrastructure
    shared = deps.shared
    assert infrastructure.workspace_isolation is not None, "workspace isolation is required"
    # P2: the detached normalization workflow can outlive cancellation of its
    # parent Run. Keep that route unavailable until cancellation propagation
    # and descendant-stop confirmation are established.
    infrastructure.sandbox_manager.configure_file_document_router(None)
    trace_repository = PostgresTraceRepository(worker_database_url)
    event_factory = TracingWebEventSinkFactory(
        RedisWebRunEventSinkFactory(infrastructure.redis_client),
        trace_repository,
    )
    lifecycle = WebRunLifecycleService(
        worker_database_url,
        TraceLifecycleObserver(trace_repository, event_factory.detach_run),
    )
    context = ContextAssemblyService(
        worker_database_url, shared.context_builder, shared.hindsight_client
    )
    # Chat resources use the same account locks and Sandbox as QQ.
    resource_prep = SessionResourceRecoveryService(
        worker_database_url,
        infrastructure.sandbox_manager,
        infrastructure.workspace_isolation.account_locks,
        infrastructure.git_repo_manager,
        infrastructure.run_file_workspace,
    )
    loader = PostgresWebRequestLoader(worker_database_url, context)
    agent_store = AgentDataStore(
        worker_database_url,
        lease_ttl_seconds=config.temporal.agent_execution_lease_ttl_seconds,
    )
    from conversation_domain.run_input import ChatRunInputLoader

    lifecycle_activities = RunLifecycleActivities(
        lifecycle,
        ChatRunInputLoader(agent_store, max_turns=config.agent.max_tool_turns),
        event_factory,
    )
    canonicalizer = W3libSourceCanonicalizer()
    research_activities = ResearchActivities(
        worker_database_url,
        CompositeSourceDiscoveryProvider([
            SearXNGDiscoveryProvider(
                config.research.searxng_url,
                timeout_seconds=config.research.search_timeout_seconds,
            ),
            GitHubDiscoveryProvider(),
            RSSDiscoveryProvider(timeout_seconds=config.research.search_timeout_seconds),
        ]),
        StaticWebContentProvider(
            canonicalizer,
            browser=PlaywrightBrowserFetchProvider(
                timeout_seconds=config.research.browser_timeout_seconds
            ),
            timeout_seconds=config.research.fetch_timeout_seconds,
            min_content_chars=config.research.min_content_chars,
        ),
        canonicalizer,
        ResourcePoolResearchSynthesizer(infrastructure.resource_pool),
        max_sources=config.research.max_sources,
        max_fetches=config.research.max_fetches,
        max_iterations=config.research.max_iterations,
        min_evidence=config.research.min_evidence,
        min_distinct_sources=config.research.min_distinct_sources,
        markdown_publisher=(
            ResearchMarkdownPublisher(infrastructure.file_output_publisher)
            if infrastructure.file_output_publisher is not None else None
        ),
    )
    durable_activities = DurableAgentActivities(
        context_bindings=ChatExecutionBindings(),
        store=agent_store,
        loader=loader,
        brain=shared.brain_engine,
        actions=shared.action_runtime,
        event_factory=event_factory,
        resource_prep=resource_prep,
        lifecycle=lifecycle,
        run_budget=RunBudgetService(worker_database_url),
        approval_service=FileActionApprovalService(worker_database_url),
    )
    artifact_build = ArtifactBuildService(
        worker_database_url,
        WebArtifactGenerator(
            infrastructure.resource_pool,
            max_bytes=int(os.getenv("ARTIFACT_HTML_MAX_BYTES", str(1024 * 1024))),
        ),
    )
    artifact_activities = ArtifactActivities(artifact_build)
    segment_activities = SegmentActivities(durable_activities.store)
    workers = build_web_temporal_workers(
        client,
        lifecycle_activities=[
            lifecycle_activities.prepare_run,
            lifecycle_activities.load_agent_run_input,
            lifecycle_activities.finalize_failed,
            lifecycle_activities.finalize_cancelled,
            durable_activities.finalize_agent_result,
            artifact_activities.execute,
            research_activities.prepare_research_activity,
            research_activities.trigger_scheduled_research_activity,
            research_activities.create_research_plan_activity,
            research_activities.discover_research_sources_activity,
            research_activities.rank_research_sources_activity,
            research_activities.fetch_research_sources_activity,
            research_activities.normalize_research_sources_activity,
            research_activities.extract_research_evidence_activity,
            research_activities.assess_research_corroboration_activity,
            research_activities.analyze_research_gaps_activity,
            research_activities.synthesize_research_report_activity,
            research_activities.verify_research_citations_activity,
            research_activities.compare_previous_research_activity,
            research_activities.publish_research_artifact_activity,
            research_activities.save_research_workspace_activity,
            research_activities.complete_research_activity,
            research_activities.fail_research_activity,
        ],
        agent_activities=[
            segment_activities.acquire,
            segment_activities.release,
            segment_activities.begin_wait,
            segment_activities.finish_wait,
            durable_activities.context_bootstrap,
            durable_activities.model_decision,
            durable_activities.tool_execution,
            durable_activities.file_action_approval_status,
            durable_activities.planning,
            durable_activities.evaluate_plan,
        ],
    )
    execution_store = PostgresWorkflowExecutionStore(worker_database_url)
    dispatcher = WebOutboxDispatcher(
        OutboxService(worker_database_url),
        TemporalOutboxDispatcher(
            execution_store,
            TemporalClientAdapter(client),
            lifecycle,
        ),
        worker_id=f"hpagent-web-dispatcher-{os.getpid()}",
    )
    reconciler = WebRunReconciler(
        LifecycleReconcileStore(execution_store, lifecycle),
        TemporalInspectorAdapter(client),
    )
    # Phase F: Memory Retention consumer only starts when Hindsight is available.
    # If it is not, retain_memory rows stay pending and are consumed on a later
    # healthy boot (doc §46) — never dead-lettered just because Hindsight was down.
    memory_retention = None
    if shared.hindsight_client is not None:
        from application.memory_retention import MemoryRetentionService

        memory_retention = MemoryRetentionService(
            worker_database_url, shared.hindsight_client
        )
        logger.info("MemoryRetentionService composed (retain_memory consumer)")
    artifact_dispatcher = ArtifactOutboxDispatcher(
        ArtifactOutboxService(worker_database_url), TemporalArtifactClient(client),
        worker_id=f"hpagent-artifact-dispatcher-{os.getpid()}",
    )
    logger.info("Web real-Agent composition passed C-07 gate")
    return DurableRuntimeComposition(
        workers, dispatcher, reconciler, memory_retention, artifact_dispatcher
    )


class BackgroundTasks:
    """Composition-owned set of cancellable process background tasks."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def create(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        return task

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _build_web_background_tasks(
    *,
    tasks: BackgroundTasks,
    web_dispatcher,
    web_reconciler,
    web_memory_retention,
    lease_timeout_seconds: int,
    recovery_interval_seconds: float,
    artifact_dispatcher=None,
) -> None:
    """把 Web 组合产物变成可取消的后台任务，供 ``start_worker`` 前台运行。

    Dispatcher / Reconciler / 过期 lease 恢复 / Memory Retention（Phase F）各占
    一条独立任务，从不共享 lease 所有权。``web_memory_retention`` 必须来自
    ``DurableRuntimeComposition.memory_retention``（组合层）；它**不在**
    ``composition.workers`` 上 —— 那里只有 lifecycle/agent（C-07）。
    """
    # Canonical Web/QQ workers are composed here without module-level Temporal side effects.
    from orchestration.artifact_dispatcher import (
        run_artifact_dispatcher_loop,
        run_artifact_outbox_recovery_loop,
    )
    from orchestration.web_dispatcher import run_web_outbox_recovery_loop

    tasks.create(_run_web_dispatcher_loop(web_dispatcher))
    tasks.create(
        _run_web_reconciler_loop(web_reconciler)
    )
    # Expired-lease auto-recovery runs on its own cadence, never in the fast
    # Dispatcher poll; validate_web_worker_startup already guaranteed both
    # values are positive and interval < timeout.
    tasks.create(
        run_web_outbox_recovery_loop(
            web_dispatcher.outbox,
            lease_timeout_seconds,
            recovery_interval_seconds,
        )
    )
    if web_memory_retention is not None:
        # Phase F: retain_memory consumer + its own expired-lease sweep.  Both
        # run independently of the start/cancel Dispatcher and never share its
        # lease ownership.
        from orchestration.memory_retention_worker import (
            run_memory_retention_loop,
        )

        tasks.create(
            run_memory_retention_loop(
                web_dispatcher.outbox,
                web_memory_retention,
                worker_id=f"hpagent-memory-{os.getpid()}",
            )
        )
        tasks.create(
            run_web_outbox_recovery_loop(
                web_dispatcher.outbox,
                lease_timeout_seconds,
                recovery_interval_seconds,
                event_types={"retain_memory"},
            )
        )
    if artifact_dispatcher is not None:
        tasks.create(run_artifact_dispatcher_loop(artifact_dispatcher))
        tasks.create(
            run_artifact_outbox_recovery_loop(
                artifact_dispatcher.outbox,
                lease_timeout_seconds,
                recovery_interval_seconds,
            )
        )


@dataclasses.dataclass
class SharedInfrastructure:
    """Process-owned infrastructure shared by canonical capabilities."""

    sandbox_manager: SandboxManager
    workspace_root: Path
    mcp_manager: object | None
    resource_pool: ResourcePool
    git_repo_manager: GitRepoManager
    group_context: object | None
    redis_client: object | None
    workspace_isolation: WorkspaceIsolationRuntime
    run_file_workspace: object | None
    tenant_file_store: object | None
    file_output_publisher: object | None


@dataclasses.dataclass
class WorkerDependencies:
    """Typed results of infrastructure, shared Agent and QQ composition.

    The attached exit stack owns all acquired process resources and unwinds
    them in reverse acquisition order.
    """
    infrastructure: SharedInfrastructure
    shared: SharedAgentServices
    qq: QQSurfaceServices
    scheduler: TaskScheduler
    resource_stack: AsyncExitStack

    async def close(self) -> None:
        await self.resource_stack.aclose()



async def setup_tools(config: AppConfig):
    """启动时加载共享工具基础设施（MCP + Skills + RAG）。

    本地工具（fs_read 等）在 per-session 沙箱创建时才实例化，
    因为它们需要绑定 workspace 路径。

    Returns:
        (mcp_manager, skill_definitions, retriever)
    """
    # RAG 检索器
    retriever = None
    if config.models.tool_rag.enabled:
        try:
            from resources.embedding import create_embedding_client
            from resources.reranker import create_reranker_client
            from sandbox.tools.retriever import ToolRetriever, ToolVectorStore
            emb_client = create_embedding_client(config.models)
            reranker_client = create_reranker_client(config.models)
            vector_store = ToolVectorStore(persist_path=config.models.tool_rag.persist_path)
            retriever = ToolRetriever(vector_store, emb_client, reranker_client=reranker_client)
            logger.info("Tool RAG enabled: persist=%s top_k=%d reranker=%s",
                        config.models.tool_rag.persist_path, config.models.tool_rag.top_k,
                        "enabled" if reranker_client else "disabled")
        except Exception as e:
            logger.warning("Tool RAG init failed, falling back to full injection: %s", e)

    # MCP 工具（可选，默认不连接）
    mcp_mgr = None
    if config.models.mcp.auto_connect:
        try:
            from sandbox.tools.adapters.mcp import MCPToolManager
            mcp_mgr = MCPToolManager(config_path=config.models.mcp.config_path)
            await mcp_mgr.load_config()
            await mcp_mgr.connect()
            logger.info("MCP tools registered: %d servers, %d tools",
                        mcp_mgr.server_count, len(mcp_mgr.get_cached_tools()))
        except Exception as e:
            logger.warning("MCP tools loading failed: %s", e)
            if mcp_mgr is not None:
                try:
                    await mcp_mgr.disconnect()
                except Exception:
                    logger.warning("MCP partial startup cleanup failed", exc_info=True)
            mcp_mgr = None

    # Skills（可选）—— 支持两种格式:
    #   1. *.yaml 直接放在 skills_path 下（HpAgent 原生流水线格式）
    #   2. */SKILL.md 放在子目录中（agentskills.io 业界标准格式）
    skill_definitions = []
    if config.models.skills.enabled:
        try:
            from pathlib import Path

            import yaml

            from sandbox.tools.skills.skillmd import parse_skillmd, skillmd_to_definition

            skills_path = Path(config.models.skills.config_path)

            # 1. 加载 HpAgent 原生 YAML 格式 (tools/skills/*.yaml)
            for skill_file in skills_path.glob("*.yaml"):
                skill_def = yaml.safe_load(skill_file.read_text())
                skill_def.setdefault("type", "pipeline")
                skill_definitions.append(skill_def)

            # 2. 加载 SKILL.md 业界标准格式 (tools/skills/*/SKILL.md)
            for skillmd_file in skills_path.glob("*/SKILL.md"):
                try:
                    fm, body = parse_skillmd(skillmd_file)
                    skill_def = skillmd_to_definition(fm, body)
                    skill_definitions.append(skill_def)
                except ValueError as e:
                    logger.warning("SKILL.md parse failed: %s - %s", skillmd_file, e)

            logger.info("Skills loaded: %d from %s", len(skill_definitions), skills_path)
        except Exception as e:
            logger.warning("Skills loading failed: %s", e)

    return mcp_mgr, skill_definitions, retriever


def _build_nsjail_config(sandbox: SandboxConfig) -> NsjailConfig:
    """从 SandboxConfig 构建 NsjailConfig。

    只传递 SandboxConfig 中与 NsjailConfig 字段名匹配的值，
    其余字段使用 NsjailConfig 自身默认值。
    """
    nsjail_fields = {f.name for f in dataclasses.fields(NsjailConfig)}
    kwargs = {
        k: v for k, v in dataclasses.asdict(sandbox).items()
        if k in nsjail_fields
    }
    return NsjailConfig(**kwargs)


async def init_dependencies(config: AppConfig) -> WorkerDependencies:
    resource_stack = AsyncExitStack()
    await resource_stack.__aenter__()
    try:
        dependencies = await _init_dependencies(config, resource_stack)
        await resource_stack.aclose()
        return dependencies
    except BaseException:
        await resource_stack.aclose()
        raise


async def _init_dependencies(
    config: AppConfig, resource_stack: AsyncExitStack
) -> WorkerDependencies:
    """初始化共享依赖、统一 Agent Facade 与 Surface adapters。

    初始化顺序（严格遵守拓扑依赖 DAG）：
      1. CredentialManager → ResourcePool
      2. Redis（可选）
      3. NsjailConfig
      4. setup_tools() → mcp_mgr, skill_definitions, retriever
      5. workspace_root + file/workspace capabilities
      6. SandboxManager（依赖 3+4+5）
      7. PromptLoader → HindsightClient → HarnessContextBuilder
      8. PostgresAccountService + ChannelRouter
      9. PG-backed QQ command ingress
     10. Shared BrainEngine + ActionRuntime capabilities
     11. scheduled reflection / metrics application services
    """
    # Hard gate: no shared-worktree Agent process starts without an explicit,
    # validated isolation topology and its OS process lock.
    workspace_isolation = WorkspaceIsolationRuntime(
        workspace_root=Path(config.workspace.root),
        mode=config.workspace.workspace_isolation_mode,
        agent_worker_replicas=config.workspace.agent_worker_replicas,
        prefork_enabled=config.workspace.prefork_enabled,
        agent_activity_processes=config.workspace.agent_activity_processes,
        hosts_share_lock_registry=True,
    )
    workspace_isolation.start()
    resource_stack.callback(workspace_isolation.close)

    # ── 1. 凭据 + 资源池 ──
    credential_manager = CredentialManager()
    all_endpoints: list[ModelEndpoint] = []
    category_ids: Dict[str, list[str]] = {}

    for category in ("fast", "chat", "embedding", "image", "reasoning"):
        chain = config.models.get_chain(category)
        if not chain:
            continue
        ids: list[str] = []
        for index, entry in enumerate(chain):
            # endpoint 是“调用配置实例”，不能只用 provider:model 标识。
            # 同一远端模型在 fast/chat/reasoning 中可能有不同的超时、
            # max_tokens 和 extra_body，必须分别注册。
            endpoint_id = f"{category}:{index}:{entry.provider}:{entry.model}"
            ep = config.models.resolve_endpoint(entry, endpoint_id=endpoint_id)
            all_endpoints.append(ep)
            ids.append(endpoint_id)
        if ids:
            category_ids[category] = ids
    if not all_endpoints:
        raise RuntimeError("No models configured in models.yaml")

    credential_manager.register_model_chain(all_endpoints)
    worker_database_url = os.getenv("WORKER_DATABASE_URL")
    if not worker_database_url:
        raise RuntimeError("WORKER_DATABASE_URL is required for governed model invocation")
    resource_pool = ResourcePool(
        credential_manager,
        entitlement_service=EntitlementService(worker_database_url),
        budget_coordinator=ModelBudgetCoordinator(worker_database_url),
        snapshot_repository=SnapshotRepository(worker_database_url),
    )
    await resource_pool.initialize_models()
    for category, ids in category_ids.items():
        resource_pool.configure_fallback_group(category, ids)
        logger.info("Model chain configured: category=%s endpoints=%s", category, ids)
    if "chat" in category_ids:
        resource_pool.configure_fallback_group("default", category_ids["chat"])
        logger.info("Model chains registered: %s", ", ".join(
            f"{c}={len(ids)}" for c, ids in category_ids.items()
        ))

    # ── 2. Redis ──
    redis_cache = None
    redis_client = None  # 保留引用，供 GroupContextStore 使用
    redis_url = config.redis.url or os.getenv("REDIS_URL", "")
    if redis_url:
        try:
            import redis.asyncio as aioredis

            from storage.redis import RedisCache
            redis_client = aioredis.from_url(redis_url, decode_responses=False)
            resource_stack.push_async_callback(redis_client.aclose)
            redis_cache = RedisCache(redis_client, default_ttl=config.redis.default_ttl)
            logger.info("Redis connected: %s", redis_url)
        except Exception as e:
            redis_client = None
            redis_cache = None
            logger.warning("DEGRADATION: Redis unavailable (%s) → falling back to in-memory storage", e)

    # ── 2b. 群聊短期上下文缓存（依赖 Redis 客户端）──
    group_context = None
    if redis_client:
        from memory.group_context import GroupContextStore
        group_context = GroupContextStore(
            redis_client,
            window_size=config.redis.group_context_window,
            ttl_seconds=config.redis.group_context_ttl,
            density_threshold=config.redis.group_context_density_threshold,
        )
        logger.info(
            "GroupContextStore initialized: window=%d ttl=%ds density_threshold=%.1f",
            config.redis.group_context_window, config.redis.group_context_ttl,
            config.redis.group_context_density_threshold,
        )
    else:
        logger.info("GroupContextStore: skipped (no Redis, group context disabled)")

    # ── 3. nsjail 配置 ──
    if config.sandbox.nsjail_enabled:
        nsjail_config = _build_nsjail_config(config.sandbox)
        logger.info(
            "Nsjail configured: time=%ds mem=%dMB cpu=%ds net=%s",
            nsjail_config.time_limit,
            nsjail_config.memory_limit_mb,
            nsjail_config.cpu_limit_seconds,
            "off" if nsjail_config.disable_network else "on",
        )
    else:
        nsjail_config = None
        logger.info("Nsjail disabled")

    # ── 4. 共享工具基础设施（MCP + Skills + RAG）────
    # 必须在 SandboxManager 之前调用，因为 SandboxManager 需要这些产物
    mcp_mgr, skill_definitions, retriever = await setup_tools(config)
    if mcp_mgr is not None:
        resource_stack.push_async_callback(mcp_mgr.disconnect)

    # ── 5. 工作区能力 ──
    workspace_root = Path(config.workspace.root)
    logger.info("Workspace root initialized: root=%s", workspace_root)
    run_file_workspace = None
    file_output_publisher = None
    file_conversion_provider = None
    file_capability = FileCapabilityConfig.from_environment(
        workspace_root, os.getenv("WORKER_DATABASE_URL")
    )
    if file_capability.upload_enabled:
        worker_database_url = os.getenv("WORKER_DATABASE_URL")
        assert worker_database_url is not None
        assert file_capability.store_root is not None
        assert file_capability.run_root is not None
        tenant_store = TenantFileStore(
            file_capability.store_root,
            max_bytes=file_capability.max_bytes,
        )
        run_file_workspace = RunFileWorkspace(
            worker_database_url, tenant_store, file_capability.run_root
        )
        from file_runtime import OutputPublisher

        file_output_publisher = OutputPublisher(worker_database_url, tenant_store)
        logger.info("Run file workspace enabled with isolated object/execution roots")
        if file_capability.transform_enabled:
            from file_adapters import GotenbergConversionProvider
            gotenberg_url = os.getenv("GOTENBERG_URL", "").strip()
            if not gotenberg_url:
                raise RuntimeError("file transforms require GOTENBERG_URL")
            file_conversion_provider = GotenbergConversionProvider(
                gotenberg_url, max_output_bytes=file_capability.max_bytes
            )

    # ── 5b. 定时调度器 ──
    scheduler: TaskScheduler = TaskScheduler(data_dir=Path(config.scheduler.data_dir))
    logger.info("TaskScheduler initialized: data_dir=%s poll_interval=%.1fs",
                config.scheduler.data_dir, config.scheduler.poll_interval)

    # ── 6. 沙箱管理器（依赖 3+4+5）────
    sandbox_manager = SandboxManager(
        nsjail_config=nsjail_config,
        redis_cache=redis_cache,
        data_root=workspace_root,
        max_idle_seconds=config.sandbox.max_idle_seconds,
        mcp_manager=mcp_mgr,
        skill_definitions=skill_definitions,
        retriever=retriever,
        native_tools_enabled=config.sandbox.native_tools_enabled,
        nsjail_enabled=config.sandbox.nsjail_enabled,
        file_tools_enabled=(
            file_capability.upload_enabled
        ),
        file_output_publisher=file_output_publisher,
        file_conversion_provider=file_conversion_provider,
        scheduler=scheduler,
    )
    resource_stack.callback(sandbox_manager.close)

    # ── 7. Prompt + Hindsight + 上下文构建器 ──
    prompt_loader = PromptLoader(config.prompts)
    logger.debug("PromptLoader initialized from AppConfig.prompts")

    from memory.hindsight_client import HindsightClient
    hindsight_client = None
    if config.hindsight.enabled:
        try:
            hindsight_client = HindsightClient(
                base_url=config.hindsight.base_url,
                api_key=config.hindsight.api_key,
                timeout=config.hindsight.timeout,
                prompt_loader=prompt_loader,
                retain_mission=config.hindsight.retain_mission,
                reflect_mission=config.hindsight.reflect_mission,
                retain_timeout=config.hindsight.retain_timeout,
                recall_timeout=config.hindsight.recall_timeout,
                reflect_timeout=config.hindsight.reflect_timeout,
            )
            logger.info("HindsightClient initialized: base_url=%s", hindsight_client.base_url)
        except Exception as e:
            logger.warning("DEGRADATION: Hindsight unavailable (%s) → long-term memory disabled", e)

    # ── 8. Shared and surface composition ──
    channel_router = ChannelRouter()
    # ── 11. GitRepoManager ──
    git_repo_manager = GitRepoManager(repos_root=workspace_root)
    logger.info("GitRepoManager initialized: repos_root=%s", workspace_root)

    shared = build_shared_agent_services(
        config=config,
        worker_database_url=os.getenv("WORKER_DATABASE_URL"),
        sandbox_manager=sandbox_manager,
        resource_pool=resource_pool,
        hindsight_client=hindsight_client,
        prompt_loader=prompt_loader,
    )
    qq = build_qq_surface_services(
        channel_router=channel_router,
        group_context=group_context,
        prompt_loader=prompt_loader,
    )
    logger.info("Shared Agent capabilities and QQ protocol services assembled")
    return WorkerDependencies(
        infrastructure=SharedInfrastructure(
            sandbox_manager=sandbox_manager,
            workspace_root=workspace_root,
            mcp_manager=mcp_mgr,
            resource_pool=resource_pool,
            git_repo_manager=git_repo_manager,
            group_context=group_context,
            redis_client=redis_client,
            workspace_isolation=workspace_isolation,
            run_file_workspace=run_file_workspace,
            tenant_file_store=(tenant_store if file_capability.upload_enabled else None),
            file_output_publisher=file_output_publisher,
        ),
        shared=shared,
        qq=qq,
        scheduler=scheduler,
        resource_stack=resource_stack.pop_all(),
    )


async def start_worker(config: AppConfig) -> None:
    """完整启动流程: 组装依赖 → 连接 Temporal → 启动 Worker + 渠道监听。"""
    deps = await init_dependencies(config)

    active_channels: list = []
    background_tasks = BackgroundTasks()
    runtime_stop_attempted = False
    try:
        scheduled_memory = ScheduledMemoryActivities(
            memory_reflection=deps.shared.memory_reflection,
            metrics=deps.shared.metrics,
        )

        # ── 注册提醒 handler ──
        async def _handle_user_reminder(task):
            """发送用户提醒消息。"""
            ch_str = task.params.get("channel_type", "napcat")
            try:
                ch_type = ChannelType(ch_str)
            except ValueError:
                ch_type = ChannelType.NAPCAT

            metadata = task.params.get("metadata", {})
            content = f"[提醒] {task.params.get('content', '')}"

            # 群聊中 @ 回原用户
            sender_id = task.params.get("sender_id", "")
            if metadata.get("detail_type") == "group" and sender_id:
                content = f"[CQ:at,qq={sender_id}] {content}"

            msg = UnifiedMessage(
                session_id=f"reminder-{task.id}",
                account_id=task.params.get("account_id", ""),
                sender_id=sender_id,
                channel_type=ch_type,
                content=content,
                metadata=metadata,
            )
            await deps.qq.channel_router.send(msg)

        deps.scheduler.register_handler("user_reminder", _handle_user_reminder)

        # ── 加载持久化任务 + 注入 scheduler 到 reminder 模块 + 启动轮询 ──
        if config.scheduler.enabled:
            await deps.scheduler.load()
            background_tasks.create(
                deps.scheduler.poll_loop(interval=config.scheduler.poll_interval)
            )
            logger.info("TaskScheduler poll_loop started")
        else:
            logger.info("TaskScheduler disabled by config")

        # ── 连接 Temporal ──
        client = await Client.connect(config.temporal.host)

        # ── 创建 Worker ──
        worker = Worker(
            client,
            task_queue=config.temporal.task_queue,
            workflows=[ReflectWorkflow, MetricsReportWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            activities=[
                scheduled_memory.reflect,
                scheduled_memory.reflect_batch,
                scheduled_memory.metrics_report,
            ],
        )

        web_workers = None
        web_dispatcher = None
        web_reconciler = None
        web_memory_retention = None
        web_artifact_dispatcher = None
        research_schedule_manager = None
        composition = compose_durable_runtime(client, config, deps)
        web_workers = composition.workers
        web_dispatcher = composition.dispatcher
        web_reconciler = composition.reconciler
        web_memory_retention = composition.memory_retention
        web_artifact_dispatcher = composition.artifact_dispatcher
        from orchestration.research_schedule import ResearchScheduleManager

        research_schedule_manager = ResearchScheduleManager(
            os.environ["WORKER_DATABASE_URL"], client
        )

        # ── 渠道注册（按 config.yaml 的 channels.enabled 列表动态加载）──
        _channel_factories = {
            ChannelType.NAPCAT: NapCatChannel,
            ChannelType.OFFICIAL_QQ: OfficialQQChannel,
        }

        active_channels: list = []
        for ch_name in config.channels.enabled:
            try:
                ch_type = ChannelType(ch_name)
            except ValueError:
                logger.warning("Unknown channel in config: %s, skipped", ch_name)
                continue

            factory = _channel_factories.get(ch_type)
            if factory is None:
                logger.warning("No implementation for channel: %s, skipped", ch_name)
                continue

            channel = factory()
            if hasattr(channel, "bot_name"):
                channel.bot_name = getattr(config.prompts, "bot_name", "bot")
            deps.qq.channel_router.register(ch_type, channel)
            active_channels.append(channel)
            logger.info("Channel registered: %s", ch_name)

        from conversation_domain.commands import CommandService
        from conversation_domain.surface_commands import SurfaceConversationCommands

        conversation_service = ConversationService(SurfaceConversationCommands(
            CommandService(
                os.environ["WORKER_DATABASE_URL"],
                budget_mode=os.getenv("RUN_BUDGET_MODE", "observe"),
                budget_policy_version=os.getenv("RUN_BUDGET_POLICY_VERSION", "file-p0-v1"),
            )
        ))
        ingress_service = MessageIngressService(
            group_context=deps.infrastructure.group_context,
            conversation_service=conversation_service,
            reply_service=deps.qq.reply_service,
            identity_command_service=IdentityCommandService(
                IdentityBindingService(
                    os.environ["WORKER_DATABASE_URL"],
                    load_worker_qq_binding_code_pepper(),
                    int(os.getenv("QQ_BINDING_CHALLENGE_SECONDS", "300")),
                )
            ),
        )

        async def handle_message(message: UnifiedMessage) -> None:
            await ingress_service.handle(message)

        # ── 并发运行 Worker + 渠道监听 ──
        background_tasks.create(
            _run_sandbox_cleanup_loop(
                deps.infrastructure.sandbox_manager, interval=300
            )
        )
        if deps.infrastructure.run_file_workspace is not None:
            from web_domain.file_cleanup import FileCleanupService

            cleanup_interval = int(os.getenv("FILE_CLEANUP_INTERVAL_SECONDS", "300"))
            if cleanup_interval <= 0:
                raise RuntimeError("FILE_CLEANUP_INTERVAL_SECONDS must be positive")
            background_tasks.create(
                _run_file_cleanup_loop(
                    FileCleanupService(
                        deps.infrastructure.run_file_workspace.database,
                        deps.infrastructure.run_file_workspace.store,
                    ),
                    interval=cleanup_interval,
                )
            )
        async with AsyncExitStack() as worker_stack:
            await worker_stack.enter_async_context(worker)
            if (
                web_workers is not None
                and web_dispatcher is not None
                and web_reconciler is not None
            ):
                await worker_stack.enter_async_context(web_workers.lifecycle)
                await worker_stack.enter_async_context(web_workers.agent)
                _build_web_background_tasks(
                    tasks=background_tasks,
                    web_dispatcher=web_dispatcher,
                    web_reconciler=web_reconciler,
                    web_memory_retention=web_memory_retention,
                    artifact_dispatcher=web_artifact_dispatcher,
                    lease_timeout_seconds=config.temporal.web_outbox_lease_timeout_seconds,
                    recovery_interval_seconds=config.temporal.web_outbox_recovery_interval_seconds,
                )
                if research_schedule_manager is not None:
                    from orchestration.research_schedule import (
                        run_research_schedule_reconciler_loop,
                    )

                    background_tasks.create(
                        run_research_schedule_reconciler_loop(research_schedule_manager)
                    )
            for ch in active_channels:
                await ch.start_monitor(handle_message)

            from application.qq_delivery import QQDeliveryAdapter, QQDeliveryService

            background_tasks.create(
                QQDeliveryService(
                    os.environ["WORKER_DATABASE_URL"],
                    QQDeliveryAdapter(deps.qq.channel_router),
                ).run()
            )
            channel_names = [ch.channel_type.value for ch in active_channels]
            logger.info(
                "Orchestration Worker started on task_queue='%s' (channels: %s)",
                config.temporal.task_queue,
                ", ".join(channel_names) if channel_names else "none",
            )

            # 设置定期记忆反思 Schedule
            await _setup_reflect_schedule(client, deps.shared.account_service, config)

            # 设置定期指标报告 Schedule（每 30 分钟）
            await _setup_metrics_schedule(client, config)

            logger.info(
                "Periodic schedules configured: reflect=every-%dh metrics=every-30m",
                config.agent.reflect_interval_hours,
            )

            try:
                await asyncio.Future()
            finally:
                # Stop ingress and dispatcher producers before Temporal Worker
                # contexts drain, then release shared infrastructure outside.
                runtime_stop_attempted = True
                await _stop_worker_runtime(
                    active_channels=active_channels,
                    background_tasks=background_tasks,
                )
    finally:
        if not runtime_stop_attempted:
            await _stop_worker_runtime(
                active_channels=active_channels,
                background_tasks=background_tasks,
            )
        await deps.close()
        logger.info("Worker shutdown complete")


async def _stop_worker_runtime(
    *,
    active_channels,
    background_tasks: BackgroundTasks,
) -> None:
    """Stop ingress and background producers before Temporal Workers drain."""
    logger.info("Worker stopping channels and background tasks...")

    for ch in active_channels:
        ch_name = ch.channel_type.value
        try:
            await ch.stop_monitor()
            logger.info("%s monitor stopped", ch_name)
        except Exception as e:
            logger.warning("%s stop_monitor failed: %s", ch_name, e)

    await background_tasks.close()


async def _run_sandbox_cleanup_loop(sandbox_manager, interval: int = 300) -> None:
    """后台任务：每 interval 秒清理一次闲置沙箱。"""
    while True:
        try:
            await asyncio.sleep(interval)
            cleaned = sandbox_manager.cleanup_idle_sandboxes()
            if cleaned > 0:
                logger.info("Sandbox cleanup: %d idle sandboxes destroyed", cleaned)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Sandbox cleanup error: %s", e)


async def _run_file_cleanup_loop(service, interval: int = 300) -> None:
    """Delete expired unbound objects without blocking the event loop."""
    while True:
        try:
            result = await asyncio.to_thread(service.cleanup_once)
            if result.claimed:
                logger.info(
                    "File cleanup: claimed=%d deleted=%d failed=%d",
                    result.claimed,
                    result.deleted,
                    result.failed,
                )
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("File cleanup iteration failed")
            await asyncio.sleep(interval)


async def _run_web_dispatcher_loop(dispatcher, idle_seconds: float = 0.25) -> None:
    """Continuously consume Web start/cancel Outbox events."""
    while True:
        try:
            handled = await dispatcher.run_once()
            if not handled:
                await asyncio.sleep(idle_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Web Outbox Dispatcher iteration failed")
            await asyncio.sleep(1)


async def _run_web_reconciler_loop(reconciler, interval_seconds: float = 5) -> None:
    """Periodically converge active Runs and Temporal execution facts."""
    while True:
        try:
            await reconciler.run_once()
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Web Reconciler iteration failed")
            await asyncio.sleep(interval_seconds)


async def _setup_reflect_schedule(client, account_service, config) -> None:
    """创建 Temporal Schedule，每 6 小时触发一次 ReflectWorkflow。

    ReflectWorkflow 遍历所有已知账号，调用 reflect_activity
    触发 Hindsight 深度推理（记忆关联、矛盾检测、知识抽象、经验总结）。
    """
    from datetime import timedelta

    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleOverlapPolicy,
        SchedulePolicy,
        ScheduleSpec,
    )

    schedule_id = "hpagent-reflect-schedule"
    try:
        # 先清理旧 schedule 再创建（upsert 语义，消除重启时的 "Schedule already running" 警告）
        try:
            handle = client.get_schedule_handle(schedule_id)
            await handle.delete()
        except Exception:
            pass

        account_ids = account_service.list_all_ids()
        if not account_ids:
            logger.info("Reflect schedule skipped: no accounts registered")
            return

        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    ReflectWorkflow.run,
                    args=[account_ids],
                    id=f"hpagent-reflect-{len(account_ids)}",
                    task_queue=config.temporal.task_queue,
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(
                        every=timedelta(hours=config.agent.reflect_interval_hours)
                    )]
                ),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )
        logger.info(
            "Reflect schedule created: id=%s every=%dh accounts=%d",
            schedule_id, config.agent.reflect_interval_hours, len(account_ids),
        )
    except Exception as e:
        logger.warning("Failed to create reflect schedule: %s", e)


async def _setup_metrics_schedule(client, config) -> None:
    """创建 Temporal Schedule，每 30 分钟触发一次 MetricsReportWorkflow。

    MetricsReportWorkflow 调用 metrics_report_activity 采集 Hindsight
    可观测性指标并以结构化 JSON 日志输出，供外部监控系统（Prometheus/
    Grafana/ELK）采集。
    """
    from datetime import timedelta

    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleOverlapPolicy,
        SchedulePolicy,
        ScheduleSpec,
        ScheduleState,
    )

    schedule_id = "hpagent-metrics-schedule"
    try:
        # 先清理旧 schedule 再创建（upsert 语义）
        try:
            handle = client.get_schedule_handle(schedule_id)
            await handle.delete()
        except Exception:
            pass

        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    MetricsReportWorkflow.run,
                    id="hpagent-metrics-report",
                    task_queue=config.temporal.task_queue,
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(minutes=30))]
                ),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
                state=ScheduleState(note="Hindsight 可观测性指标报告 —— 每 30 分钟采集一次"),
            ),
        )
        logger.info("Metrics report schedule created: id=%s every=30m", schedule_id)
    except Exception as e:
        logger.warning("Failed to create metrics schedule: %s", e)
