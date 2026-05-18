"""
Orchestration Worker —— Temporal Worker 启动与依赖组装。

启动序列:
  1. AppConfig.from_yaml() → 加载全量结构化配置
  2. init_dependencies()    → 按 config 组装所有依赖
  3. inject(harness)         → 注入 HarnessRunner 到 Activities
  4. 连接 Temporal → 启动 Worker → 渠道监听

架构:
  Temporal Workflow （纯编排）
      ↓ 只调用 Harness Activities
  HarnessRunner    （无状态协调器）
      ↓ 协调
  SessionStore / ContextBuilder / ResourcePool / SandboxManager / ChannelRouter
"""
import asyncio
import dataclasses
import logging
import os
from pathlib import Path
from types import List, Dict

from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from orchestration.config import AppConfig, SandboxConfig
from orchestration.workflow import OrchestrationWorkflow
from harness.activities import (
    inject,
    process_turn_activity,
    archive_session_activity,
    reflect_activity,
)
from harness.context_builder import HarnessContextBuilder
from harness.prompts import PromptLoader
from harness.runner import HarnessRunner
from session.store import SessionStore
from resources.resource_pool import ResourcePool
from resources.credentials import CredentialManager, ModelEndpoint
from sandbox.sandbox_manager import SandboxManager
from sandbox.nsjail import NsjailConfig
from sandbox.tools.factory import ToolFactory
from sandbox.channels.napcat import NapCatChannel
from sandbox.channels.router import ChannelRouter
from account.account_service import AccountService
from workspace.manager import WorkspaceManager
from common.types import UnifiedMessage, ChannelType

logger = logging.getLogger("HpAgent.OrchestrationWorker")


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


async def init_dependencies(config: AppConfig) -> tuple[HarnessRunner, AccountService, ChannelRouter, SandboxManager, WorkspaceManager]:
    """初始化所有共享依赖并组装 HarnessRunner。

    Returns:
        (harness_runner, account_service, channel_router, sandbox_manager,
         workspace_manager)
    """
    # ── 凭据 + 资源池 ──
    credential_manager = CredentialManager()

    # 从 models.yaml 加载所有模型分类，展开为统一端点列表
    all_endpoints: list[ModelEndpoint] = []
    category_ids: Dict[str, list[str]] = {}  # category → [model_id, ...]

    for category in ("chat", "embedding", "image", "reasoning"):
        chain = config.models.get_chain(category)
        if not chain:
            continue
        ids: list[str] = []
        for entry in chain:
            ep = config.models.resolve_endpoint(entry)
            model_id = f"{ep.provider}:{ep.model}"
            all_endpoints.append(ep)
            ids.append(model_id)
        if ids:
            category_ids[category] = ids

    if not all_endpoints:
        raise RuntimeError("No models configured in models.yaml")

    credential_manager.register_model_chain(all_endpoints)
    resource_pool = ResourcePool(credential_manager)
    await resource_pool.initialize_models()

    # 为每个类别配置降级链，并将 chat 设为默认
    for category, ids in category_ids.items():
        resource_pool.configure_fallback_group(category, ids)
    if "chat" in category_ids:
        resource_pool.configure_fallback_group("default", category_ids["chat"])
        logger.info("Model chains registered: %s", ", ".join(
            f"{c}={len(ids)}" for c, ids in category_ids.items()
        ))

    # ── Redis ──
    redis_cache = None
    redis_url = config.redis.url or os.getenv("REDIS_URL", "")
    if redis_url:
        try:
            import redis.asyncio as aioredis
            from storage.redis import RedisCache
            redis_client = aioredis.from_url(redis_url, decode_responses=False)
            redis_cache = RedisCache(redis_client, default_ttl=config.redis.default_ttl)
            logger.info("Redis connected: %s", redis_url)
        except Exception as e:
            logger.warning("DEGRADATION: Redis unavailable (%s) → falling back to in-memory storage", e)

    # ── nsjail ──
    nsjail_config = _build_nsjail_config(config.sandbox)
    logger.info(
        "Nsjail configured: time=%ds mem=%dMB cpu=%ds net=%s",
        nsjail_config.time_limit,
        nsjail_config.memory_limit_mb,
        nsjail_config.cpu_limit_seconds,
        "off" if nsjail_config.disable_network else "on",
    )

    # ── 工作区 ──
    workspace_manager = WorkspaceManager(
        root=Path(config.workspace.root),
        db_path=config.workspace.db_path or None,
    )
    logger.info("WorkspaceManager initialized: root=%s", workspace_manager.root)

    # ── Hindsight ──
    from memory.hindsight_client import HindsightClient
    hindsight_client = None
    if config.hindsight.enabled:
        try:
            hindsight_client = HindsightClient(
                base_url=config.hindsight.base_url,
                api_key=config.hindsight.api_key,
                timeout=config.hindsight.timeout,
                prompt_loader=prompt_loader,
            )
            logger.info("HindsightClient initialized: base_url=%s", hindsight_client.base_url)
        except Exception as e:
            logger.warning("DEGRADATION: Hindsight unavailable (%s) → long-term memory disabled", e)

    # ── 沙箱管理器 ──
    sandbox_manager = SandboxManager(
        nsjail_config=nsjail_config,
        redis_cache=redis_cache,
        workspace_manager=workspace_manager,
        max_idle_seconds=config.sandbox.max_idle_seconds,
    )
    # ── Prompt 加载器 ──
    prompt_loader = PromptLoader(Path("config/prompts"))
    logger.info("PromptLoader initialized from config/prompts/")

    # ── 上下文构建器 ──
    context_builder = HarnessContextBuilder(prompt_loader=prompt_loader)

    # ── 账号服务（JSON 文件持久化: data/accounts.json） ──
    account_service = AccountService(data_dir=Path(".hpagent/data"))

    # ── 渠道路由器 ──
    channel_router = ChannelRouter()

    # ── SessionStore ──
    session_store = SessionStore(
        redis_cache=redis_cache,
        hindsight_client=hindsight_client,
        backup_dir=Path(config.session.backup_dir),
    )
    logger.info(
        "SessionStore: redis=%s hindsight=%s backup=%s",
        "connected" if redis_cache else "in-memory fallback",
        "connected" if hindsight_client else "disabled",
        config.session.backup_dir,
    )

    # ── Multi-Agent Executor (mode=multi 时加载) ──
    multi_agent_executor = None
    if config.agent.mode == "multi":
        import yaml as _yaml
        agents_path = Path(config.agent.multi_agent.agents_config)
        if not agents_path.is_absolute():
            agents_path = Path(__file__).resolve().parent.parent / agents_path
        if agents_path.exists():
            with open(agents_path, "r", encoding="utf-8") as _f:
                agents_raw = _yaml.safe_load(_f) or {}
            agents_list = agents_raw.get("agents", [])
            from agent.runner import MultiAgentExecutor
            multi_agent_executor = MultiAgentExecutor(
                resource_pool=resource_pool,
                agents_config=agents_list,
                strategy=config.agent.multi_agent.strategy,
                max_review_rounds=config.agent.multi_agent.max_review_rounds,
            )
            logger.info(
                "MultiAgentExecutor: strategy=%s agents=%d",
                config.agent.multi_agent.strategy, len(agents_list),
            )
        else:
            logger.warning("Agents config not found: %s, falling back to single-agent", agents_path)

    # ── HarnessRunner ──
    harness_runner = HarnessRunner(
        session_store=session_store,
        context_builder=context_builder,
        resource_pool=resource_pool,
        sandbox_manager=sandbox_manager,
        channel_router=channel_router,
        max_tool_turns=config.agent.max_tool_turns,
        agent_mode=config.agent.mode,
        multi_agent_executor=multi_agent_executor,
    )

    logger.info("HarnessRunner assembled: all dependencies wired")
    return (
        harness_runner,
        account_service,
        channel_router,
        sandbox_manager,
        workspace_manager,
    )


async def start_worker(config: AppConfig) -> None:
    """完整启动流程: 组装依赖 → 连接 Temporal → 启动 Worker + 渠道监听。"""
    (
        harness_runner,
        account_service,
        channel_router,
        sandbox_manager,
        workspace_manager,
    ) = await init_dependencies(config)

    inject(harness=harness_runner)

    # ── 连接 Temporal ──
    client = await Client.connect(config.temporal.host)

    # ── 创建 Worker ──
    worker = Worker(
        client,
        task_queue=config.temporal.task_queue,
        workflows=[OrchestrationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activities=[
            process_turn_activity,
            archive_session_activity,
            reflect_activity,
        ],
    )

    # ── 渠道注册 ──
    napcat = NapCatChannel()
    channel_router.register(ChannelType.NAPCAT, napcat)

    async def handle_message(message: UnifiedMessage) -> None:
        if not message.content or not message.content.strip():
            return

        ch_type = (
            message.channel_type.value
            if hasattr(message.channel_type, "value")
            else str(message.channel_type)
        )

        account_id = await account_service.resolve(ch_type, message.sender_id)
        session_id = f"session-{account_id}"

        user_message = {
            "content": message.content,
            "sender_id": message.sender_id,
            "channel_type": ch_type,
            "session_id": session_id,
            "account_id": account_id,
            "metadata": message.metadata,
            "timestamp": message.timestamp,
        }

        # 准备工作区 + 沙箱（基础设施前置，幂等）
        try:
            workspace_manager.ensure_user(account_id)
            workspace_manager.create_session(
                user_uuid=account_id,
                session_id=session_id,
                task_summary=message.content[:100],
            )
            sandbox_manager.create_session_sandbox(
                user_uuid=account_id,
                session_id=session_id,
                tools=ToolFactory.create_default_tools(),
            )
        except Exception as e:
            logger.warning("Workspace/sandbox setup failed for %s: %s", session_id, e)

        from temporalio.exceptions import WorkflowAlreadyStartedError
        try:
            await client.start_workflow(
                OrchestrationWorkflow.run,
                user_message,
                id=session_id,
                task_queue=config.temporal.task_queue,
            )
            logger.info("Started new session %s (account=%s)", session_id, account_id)
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(session_id)
            await handle.signal(OrchestrationWorkflow.new_message, user_message)
            logger.info("Signaled existing session %s", session_id)
        except Exception as e:
            logger.exception("Failed to start or signal session %s: %s", session_id, e)

    # ── 并发运行 Worker + 渠道监听 ──
    async with worker:
        await napcat.start_monitor(handle_message)
        logger.info(
            "Orchestration Worker started on task_queue='%s'",
            config.temporal.task_queue,
        )
        await asyncio.Future()
