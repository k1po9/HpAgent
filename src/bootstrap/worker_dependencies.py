"""Construct shared infrastructure and transfer its resource stack to the process owner."""

from __future__ import annotations

import dataclasses
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Dict

from account.entitlement_service import EntitlementService
from application.prompts import PromptLoader
from bootstrap.qq import QQSurfaceServices, build_qq_surface_services
from bootstrap.shared_runtime import SharedAgentServices, build_shared_agent_services
from channels.router import ChannelRouter
from common.agent_policy import AgentPolicy
from common.config_validation import environment_name
from common.runtime_config import (
    DEVELOPMENT_QQ_BINDING_CODE_PEPPER as _DEVELOPMENT_QQ_BINDING_CODE_PEPPER,
)
from common.runtime_config import (
    ArtifactRuntimeConfig,
    CapacityConfig,
    FileRuntimeConfig,
    binding_challenge_seconds,
    run_budget_mode,
)
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

def load_worker_qq_binding_code_pepper() -> bytes:
    """Load the shared challenge pepper; production must configure it safely."""
    environment = environment_name(os.getenv("HPAGENT_ENV", "development"))
    configured = os.getenv("QQ_BINDING_CODE_PEPPER")
    if environment == "production" and not configured:
        raise RuntimeError(
            "QQ_BINDING_CODE_PEPPER must be explicitly set for the Worker in production"
        )
    pepper = configured.encode("utf-8") if configured else _DEVELOPMENT_QQ_BINDING_CODE_PEPPER
    if environment == "production" and len(pepper) < 32:
        raise RuntimeError("QQ_BINDING_CODE_PEPPER must contain at least 32 bytes in production")
    return pepper


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
    resource_stack: AsyncExitStack

    async def close(self) -> None:
        await self.resource_stack.aclose()


async def setup_tools(config: AppConfig):
    """启动时加载共享工具基础设施（MCP + Skills + RAG）。

    本地工具（fs_read 等）在 per-Execution 沙箱创建时才实例化，
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
            logger.info(
                "Tool RAG enabled: persist=%s top_k=%d reranker=%s",
                config.models.tool_rag.persist_path,
                config.models.tool_rag.top_k,
                "enabled" if reranker_client else "disabled",
            )
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
            logger.info(
                "MCP tools registered: %d servers, %d tools",
                mcp_mgr.server_count,
                len(mcp_mgr.get_cached_tools()),
            )
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
    kwargs = {k: v for k, v in dataclasses.asdict(sandbox).items() if k in nsjail_fields}
    return NsjailConfig(**kwargs)


async def init_dependencies(config: AppConfig) -> WorkerDependencies:
    CapacityConfig.from_environment()
    FileRuntimeConfig.from_environment()
    ArtifactRuntimeConfig.from_environment()
    binding_challenge_seconds()
    run_budget_mode(default="enforce")
    FileCapabilityConfig.from_environment(
        config.workspace.root, os.getenv("WORKER_DATABASE_URL")
    )
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
        logger.info(
            "Model chains registered: %s",
            ", ".join(f"{c}={len(ids)}" for c, ids in category_ids.items()),
        )

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
            logger.warning(
                "DEGRADATION: Redis unavailable (%s) → falling back to in-memory storage", e
            )

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
            config.redis.group_context_window,
            config.redis.group_context_ttl,
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

    # Account-bound Main Work tools share the API command service.
    from work_domain.commands import WorkCommandService

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
        file_tools_enabled=(file_capability.upload_enabled),
        file_output_publisher=file_output_publisher,
        file_conversion_provider=file_conversion_provider,
        work_commands=WorkCommandService(
            worker_database_url, budget_mode=run_budget_mode(default="enforce"),
            agent_policy=AgentPolicy.from_config(config.agent)
        ),
    )
    resource_stack.callback(sandbox_manager.close)

    # ── 7. Prompt + Hindsight + 上下文构建器 ──
    prompt_loader = PromptLoader(config.prompts)
    logger.debug("PromptLoader initialized from AppConfig.prompts")

    from memory.hindsight_client import HindsightClient

    hindsight_client = None
    if config.hindsight.enabled:
        if not config.prompts.memory:
            raise ValueError("prompts.memory is required when Hindsight is enabled")
        config.memory_quality.bank_updates(config.prompts.memory)
        try:
            hindsight_client = HindsightClient(
                base_url=config.hindsight.base_url,
                recall_enabled=config.hindsight.recall_enabled,
                api_key=config.hindsight.api_key,
                timeout=config.hindsight.timeout,
                prompt_loader=prompt_loader,
                quality_config=config.memory_quality,
                memory_prompts=config.prompts.memory,
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
        resource_stack=resource_stack.pop_all(),
    )
